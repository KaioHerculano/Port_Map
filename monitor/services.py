import logging
import re
import socket
import time
from typing import Any, List, Optional, Tuple

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import AuditLog, Device, Group, MonitorLog, MonitorTarget

logger = logging.getLogger(__name__)


class PortParserService:
    """Service to parse and create monitor targets from raw text lines."""

    @staticmethod
    @transaction.atomic
    def parse_and_create_targets(
        text: str,
        group: Optional[Group] = None,
        check_interval: int = 60,
        telegram_alert_threshold: int = 1,
    ) -> Tuple[List[MonitorTarget], List[str]]:
        lines = text.strip().split("\n")
        created_targets: List[MonitorTarget] = []
        errors: List[str] = []

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            # Parse optional label in brackets [] or parens ()
            label: Optional[str] = None
            label_match = re.search(r"(?:\[|\()(.*?)(?:\]|\))", line)
            if label_match:
                label = label_match.group(1).strip()
                line = re.sub(r"(?:\[|\()(.*?)(?:\]|\))", "", line).strip()

            if ":" not in line:
                errors.append(
                    f"Linha {line_num}: Formato inválido. Sem caractere ':'. Ex: '192.168.1.1:80'"
                )
                continue

            host, ports_str = line.rsplit(":", 1)
            host = host.strip()
            ports_str = ports_str.strip()

            if not host:
                errors.append(f"Linha {line_num}: Host/IP vazio.")
                continue

            # Parse ports
            port_parts = ports_str.split(",")
            ports: List[int] = []
            line_skipped = False

            max_ports_per_line = 100
            max_total_targets = 500

            for part in port_parts:
                part = part.strip()
                if "-" in part:
                    try:
                        start_p_str, end_p_str = part.split("-")
                        start_p = int(start_p_str.strip())
                        end_p = int(end_p_str.strip())
                        if start_p > end_p:
                            start_p, end_p = end_p, start_p

                        # Validate range boundaries first
                        if not (1 <= start_p <= 65535) or not (1 <= end_p <= 65535):
                            errors.append(
                                f"Linha {line_num}: Faixa de portas fora do limite 1-65535: '{part}'"
                            )
                            line_skipped = True
                            break

                        # Limit range size to prevent abuse/errors
                        range_size = end_p - start_p + 1
                        if range_size > max_ports_per_line:
                            errors.append(
                                f"Linha {line_num}: Faixa de portas muito grande ({range_size} portas). O limite máximo por faixa é {max_ports_per_line}."
                            )
                            line_skipped = True
                            break

                        ports.extend(range(start_p, end_p + 1))
                    except ValueError:
                        errors.append(
                            f"Linha {line_num}: Faixa de portas inválida '{part}'"
                        )
                        line_skipped = True
                        break
                else:
                    try:
                        port = int(part)
                        if not (1 <= port <= 65535):
                            errors.append(
                                f"Linha {line_num}: Porta fora do limite 1-65535: '{port}'"
                            )
                            line_skipped = True
                            break
                        ports.append(port)
                    except ValueError:
                        errors.append(f"Linha {line_num}: Porta inválida '{part}'")
                        line_skipped = True
                        break

            if line_skipped:
                continue

            # Check if this line exceeds line limit
            if len(ports) > max_ports_per_line:
                errors.append(
                    f"Linha {line_num}: Quantidade de portas ({len(ports)}) excede o limite máximo de {max_ports_per_line} por linha."
                )
                continue

            # Check if adding these ports exceeds total limit
            if len(created_targets) + len(ports) > max_total_targets:
                errors.append(
                    f"Limite máximo de {max_total_targets} alvos por importação atingido. Importação interrompida."
                )
                break

            # Create/get group if label was specified and no explicit group was provided
            line_group = group
            if not line_group and label:
                line_group, _ = Group.objects.get_or_create(name=label)

            # Create targets in DB
            for port in ports:
                try:
                    target, created = MonitorTarget.objects.update_or_create(
                        host=host,
                        port=port,
                        defaults={
                            "group": line_group,
                            "label": label,
                            "is_active": True,
                            "check_interval": check_interval,
                            "telegram_alert_threshold": telegram_alert_threshold,
                        },
                    )
                    created_targets.append(target)
                except Exception as e:
                    logger.error("Erro ao salvar alvo %s:%d: %s", host, port, str(e))
                    errors.append(
                        f"Linha {line_num}: Erro ao salvar alvo no banco de dados."
                    )

        return created_targets, errors


class MikrotikAPI:
    """Lightweight pure-python client for MikroTik RouterOS API."""

    def __init__(self, host, username, password, port=8728, timeout=3.0):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self) -> bool:
        import binascii
        import hashlib

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))

            # Initial login request
            self.write_sentence(["/login"])
            response = self.read_sentence()

            token = None
            for word in response:
                if word.startswith("!trap") or word.startswith("!fatal"):
                    return False
                if word.startswith("=ret="):
                    token = word[5:]

            if token:
                # Challenge-response flow (ROS < 6.43)
                hasher = hashlib.md5()
                hasher.update(b"\x00")
                hasher.update(self.password.encode("utf-8"))
                hasher.update(binascii.unhexlify(token))
                hashed_pass = binascii.hexlify(hasher.digest()).decode("utf-8")

                self.write_sentence(
                    ["/login", f"=name={self.username}", f"=response=00{hashed_pass}"]
                )
                response = self.read_sentence()
                for word in response:
                    if word.startswith("!trap"):
                        return False
            else:
                # Modern plain password flow (ROS >= 6.43)
                self.write_sentence(
                    ["/login", f"=name={self.username}", f"=password={self.password}"]
                )
                response = self.read_sentence()
                for word in response:
                    if word.startswith("!trap") or word.startswith("!fatal"):
                        return False
            return True
        except Exception as e:
            logger.error("MikroTik API connect failure: %s", str(e))
            return False

    def write_word(self, word: str):
        b_word = word.encode("utf-8")
        length = len(b_word)
        if length < 0x80:
            header = bytes([length])
        elif length < 0x4000:
            length |= 0x8000
            header = bytes([length >> 8, length & 0xFF])
        elif length < 0x200000:
            length |= 0xC00000
            header = bytes([length >> 16, (length >> 8) & 0xFF, length & 0xFF])
        else:
            header = bytes(
                [
                    0xF0,
                    (length >> 24) & 0xFF,
                    (length >> 16) & 0xFF,
                    (length >> 8) & 0xFF,
                    length & 0xFF,
                ]
            )
        self.sock.sendall(header + b_word)

    def write_sentence(self, sentence: List[str]):
        for word in sentence:
            self.write_word(word)
        self.sock.sendall(b"\x00")

    def read_word(self) -> Optional[str]:
        b = self.sock.recv(1)
        if not b:
            return None
        length = b[0]
        if (length & 0x80) == 0x00:
            pass
        elif (length & 0xC0) == 0x80:
            b2 = self.sock.recv(1)
            length = ((length & 0x3F) << 8) + b2[0]
        elif (length & 0xE0) == 0xC0:
            b2 = self.sock.recv(2)
            length = ((length & 0x1F) << 16) + (b2[0] << 8) + b2[1]
        elif (length & 0xF0) == 0xE0:
            b3 = self.sock.recv(3)
            length = ((length & 0x0F) << 24) + (b3[0] << 16) + (b3[1] << 8) + b3[2]
        elif (length & 0xF8) == 0xF0:
            b4 = self.sock.recv(4)
            length = (b4[0] << 24) + (b4[1] << 16) + (b4[2] << 8) + b4[3]

        if length == 0:
            return ""

        data = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8", errors="ignore")

    def read_sentence(self) -> List[str]:
        sentence = []
        while True:
            word = self.read_word()
            if word is None:
                break
            if word == "":
                break
            sentence.append(word)
        return sentence

    def talk(self, sentence: List[str]) -> List[List[str]]:
        self.write_sentence(sentence)
        reply = []
        while True:
            r = self.read_sentence()
            if not r:
                break
            reply.append(r)
            if r[0] == "!done":
                break
        return reply

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class PortCheckerService:
    """Service to test TCP connections, Ping, SNMP and RouterOS API, and save logs."""

    @staticmethod
    def _ping(host: str, timeout: float = 3.0) -> Tuple[bool, float]:
        import re
        import subprocess
        import sys

        is_windows = sys.platform.lower().startswith("win")
        if is_windows:
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]

        start = time.perf_counter()
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout + 1,
            )
            duration_ms = round((time.perf_counter() - start) * 1000, 2)

            if res.returncode == 0:
                time_match = re.search(
                    r"(?:time|tempo)[=<]\s*([\d\.]+)\s*ms", res.stdout, re.IGNORECASE
                )
                if time_match:
                    latency = float(time_match.group(1))
                else:
                    latency = duration_ms
                return True, latency
            return False, 0.0
        except subprocess.TimeoutExpired:
            return False, 0.0
        except Exception as e:
            logger.error("Ping subprocess error: %s", str(e))
            return False, 0.0

    @staticmethod
    def _parse_routeros_uptime(uptime_str: str) -> Optional[float]:
        import re

        try:
            weeks = (
                int(re.search(r"(\d+)w", uptime_str).group(1))
                if "w" in uptime_str
                else 0
            )
            days = (
                int(re.search(r"(\d+)d", uptime_str).group(1))
                if "d" in uptime_str
                else 0
            )
            hours = (
                int(re.search(r"(\d+)h", uptime_str).group(1))
                if "h" in uptime_str
                else 0
            )
            minutes = (
                int(re.search(r"(\d+)m", uptime_str).group(1))
                if "m" in uptime_str
                else 0
            )
            seconds = (
                int(re.search(r"(\d+)s", uptime_str).group(1))
                if "s" in uptime_str
                else 0
            )

            total_seconds = (
                weeks * 7 * 86400 + days * 86400 + hours * 3600 + minutes * 60 + seconds
            )
            return round(total_seconds / 86400, 2)  # in days
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_metric_value(
        sensor_type: str, label: str, val: str, raw_bps: Optional[float] = None
    ) -> Optional[float]:
        try:
            if sensor_type in ("ping", "tcp"):
                return None
            if sensor_type == "snmp_traffic" or (
                sensor_type == "mikrotik_api" and raw_bps is not None
            ):
                if raw_bps is not None:
                    return round(raw_bps / 1_000_000, 2)  # Mbps
                return None

            if val is not None:
                val_str = str(val)
                # If it's a RouterOS uptime string (contains letters w, d, h, m, s)
                if "uptime" in (label or "").lower() and any(
                    c in val_str for c in "wdhms"
                ):
                    return PortCheckerService._parse_routeros_uptime(val_str)

                cleaned = "".join(c for c in val_str if c.isdigit() or c in ".-")
                if cleaned:
                    float_val = float(cleaned)
                    # If it's SNMP uptime (ticks/hundredths of seconds), convert to days
                    if "uptime" in (label or "").lower():
                        return round(float_val / (100 * 86400), 2)
                    return float_val
        except Exception:
            pass
        return None

    @staticmethod
    def _snmp_get(
        host: str, community: str, port: int, oid: str, timeout: float = 2.0
    ) -> Tuple[bool, Optional[str]]:
        try:
            import asyncio

            from pysnmp.hlapi.asyncio import (CommunityData, ContextData,
                                              ObjectIdentity, ObjectType,
                                              SnmpEngine, UdpTransportTarget,
                                              getCmd)

            async def do_get():
                return await getCmd(
                    SnmpEngine(),
                    CommunityData(community, mpModel=1),
                    UdpTransportTarget((host, port), timeout=timeout, retries=1),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )

            loop = asyncio.new_event_loop()
            try:
                errorIndication, errorStatus, errorIndex, varBinds = (
                    loop.run_until_complete(do_get())
                )
            finally:
                loop.close()

            if errorIndication or errorStatus:
                return False, None

            if varBinds:
                return True, str(varBinds[0][1])
        except Exception as e:
            logger.error("SNMP get failed for %s (%s): %s", host, oid, str(e))
        return False, None

    @staticmethod
    def snmp_walk(
        host: str, community: str, port: int, oid: str, timeout: float = 2.0
    ) -> List[Tuple[str, str]]:
        results: List[Tuple[str, str]] = []
        try:
            import asyncio

            from pysnmp.hlapi.asyncio import (CommunityData, ContextData,
                                              ObjectIdentity, ObjectType,
                                              SnmpEngine, UdpTransportTarget,
                                              nextCmd)

            async def do_walk():
                walk_results = []
                current_oid = oid
                snmp_engine = SnmpEngine()
                while True:
                    errorIndication, errorStatus, errorIndex, varBinds = await nextCmd(
                        snmp_engine,
                        CommunityData(community, mpModel=1),
                        UdpTransportTarget((host, port), timeout=timeout, retries=1),
                        ContextData(),
                        ObjectType(ObjectIdentity(current_oid)),
                        lexicographicMode=False,
                    )
                    if errorIndication or errorStatus or not varBinds:
                        break

                    varBind = varBinds[0][0]
                    val_oid = str(varBind[0])
                    val_value = str(varBind[1])

                    if not val_oid.startswith(oid):
                        break

                    walk_results.append((val_oid, val_value))
                    current_oid = val_oid
                return walk_results

            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(do_walk())
            finally:
                loop.close()
        except Exception as e:
            logger.error("SNMP walk failed for %s (%s): %s", host, oid, str(e))
        return results

    @staticmethod
    def _format_bandwidth(bps: float) -> str:
        if bps < 1000:
            return f"{bps:.2f} bps"
        elif bps < 1000000:
            return f"{bps / 1000:.2f} kbps"
        elif bps < 1000000000:
            return f"{bps / 1000000:.2f} mbps"
        else:
            return f"{bps / 1000000000:.2f} gbps"

    @staticmethod
    def check_target(target_id: int) -> str:
        try:
            target = MonitorTarget.objects.get(id=target_id)
        except MonitorTarget.DoesNotExist:
            logger.error("Target ID %d nao encontrado.", target_id)
            return f"Target {target_id} not found."

        if not target.is_active or (target.device and not target.device.is_active):
            return f"Sensor/Target {target} esta inativo."

        # Fetch device configurations
        device = target.device
        host = device.host if device else target.host
        sensor_type = target.sensor_type

        status = False
        latency_ms = 0.0
        sensor_value = ""
        val = None
        bps = 0.0

        start_time = time.perf_counter()

        # Probing based on sensor_type
        if sensor_type == "tcp":
            port = target.port or 80
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(3.0)
                    s.connect((host, port))
                    status = True
            except Exception:
                status = False
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            sensor_value = "Online" if status else "Offline"

        elif sensor_type == "ping":
            status, latency_ms = PortCheckerService._ping(host)
            sensor_value = f"{latency_ms} ms / 0% perda" if status else "Sem resposta"

        elif sensor_type in ("snmp_numeric", "snmp_traffic"):
            comm = device.snmp_community if device else "public"
            snmp_port = device.snmp_port if device else 161
            if sensor_type == "snmp_numeric":
                oid = target.sensor_identifier
                status, val = PortCheckerService._snmp_get(host, comm, snmp_port, oid)

                # Dynamic fallback for CPU Temperature OID on MikroTik
                if oid == "1.3.6.1.4.1.14988.1.1.3.10.0" and (
                    not status
                    or val is None
                    or str(val).strip() == ""
                    or str(val).strip().lower()
                    in ("n/a", "nosuchinstance", "nosuchobject")
                ):
                    status_alt, val_alt = PortCheckerService._snmp_get(
                        host, comm, snmp_port, "1.3.6.1.4.1.14988.1.1.3.11.0"
                    )
                    if (
                        status_alt
                        and val_alt is not None
                        and str(val_alt).strip() != ""
                        and str(val_alt).strip().lower()
                        not in ("n/a", "nosuchinstance", "nosuchobject")
                    ):
                        oid = "1.3.6.1.4.1.14988.1.1.3.11.0"
                        status, val = status_alt, val_alt

                latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
                if status and val is not None:
                    val_str = str(val).strip()
                    if val_str.lower() in (
                        "n/a",
                        "no such instance currently exists at this oid",
                        "no such object",
                        "no such instance",
                        "null",
                        "none",
                        "",
                        "nosuchinstance",
                        "nosuchobject",
                    ):
                        status = False
                        sensor_value = "N/A"
                    # BGP peer state check
                    elif oid.startswith("1.3.6.1.2.1.15.3.1.2.") or oid.startswith(
                        "1.3.6.1.4.1.14988.1.1.18.1.1.2."
                    ):
                        try:
                            state_int = int(val)
                            bgp_states = {
                                1: "Idle",
                                2: "Connect",
                                3: "Active",
                                4: "OpenSent",
                                5: "OpenConfirm",
                                6: "Established",
                            }
                            state_name = bgp_states.get(
                                state_int, f"Desconhecido ({state_int})"
                            )
                            status = state_int == 6
                            sensor_value = "Estabelecido" if status else state_name
                            val = state_name
                        except ValueError:
                            status = False
                            sensor_value = f"Erro BGP: {val}"
                    # Specific formatting for CPU or Temp if labels match
                    elif (
                        "temp" in (target.label or "").lower()
                        or "temperatura" in (target.label or "").lower()
                        or oid
                        in (
                            "1.3.6.1.4.1.14988.1.1.3.10.0",
                            "1.3.6.1.4.1.14988.1.1.3.11.0",
                            "1.3.6.1.4.1.14988.1.1.3.9.0",
                        )
                    ):
                        try:
                            float_val = float(val)
                            # Typically if it is > 150, it is in tenths of a degree (e.g. 450 -> 45.0)
                            if float_val > 150:
                                float_val = float_val / 10.0
                            sensor_value = f"{float_val:.1f}ºC"
                            val = float_val
                        except (ValueError, TypeError):
                            sensor_value = f"{val}ºC" if val else "N/A"
                    elif (
                        "volt" in (target.label or "").lower()
                        or oid == "1.3.6.1.4.1.14988.1.1.3.8.0"
                    ):
                        try:
                            float_val = float(val)
                            # Voltages > 50 are usually in tenths of a volt (e.g. 241 -> 24.1)
                            if float_val > 50:
                                float_val = float_val / 10.0
                            sensor_value = f"{float_val:.1f} V"
                            val = float_val
                        except (ValueError, TypeError):
                            sensor_value = f"{val} V" if val else "N/A"
                    elif (
                        "consumo" in (target.label or "").lower()
                        or "power" in (target.label or "").lower()
                        or oid
                        in (
                            "1.3.6.1.4.1.14988.1.1.3.12.0",
                            "1.3.6.1.4.1.14988.1.1.3.14.0",
                        )
                    ):
                        try:
                            float_val = float(val)
                            # Handle different scales of power consumption (milliwatts or tenths of a watt)
                            if float_val > 500:
                                float_val = float_val / 100.0
                            elif float_val > 50:
                                float_val = float_val / 10.0
                            sensor_value = f"{float_val:.1f} W"
                            val = float_val
                        except (ValueError, TypeError):
                            sensor_value = f"{val} W" if val else "N/A"
                    elif (
                        "corrente" in (target.label or "").lower()
                        or "current" in (target.label or "").lower()
                        or oid == "1.3.6.1.4.1.14988.1.1.3.13.0"
                    ):
                        try:
                            float_val = float(val)
                            if float_val > 10:
                                float_val = (
                                    float_val / 1000.0
                                    if float_val > 100
                                    else float_val / 10.0
                                )
                            sensor_value = f"{float_val:.2f} A"
                            val = float_val
                        except (ValueError, TypeError):
                            sensor_value = f"{val} A" if val else "N/A"
                    elif (
                        "cooler" in (target.label or "").lower()
                        or "fan" in (target.label or "").lower()
                        or oid
                        in (
                            "1.3.6.1.4.1.14988.1.1.3.17.0",
                            "1.3.6.1.4.1.14988.1.1.3.18.0",
                        )
                    ):
                        try:
                            sensor_value = f"{int(float(val))} RPM"
                        except (ValueError, TypeError):
                            sensor_value = f"{val} RPM" if val else "N/A"
                    elif (
                        "psu" in (target.label or "").lower()
                        or "estado da psu" in (target.label or "").lower()
                        or oid
                        in (
                            "1.3.6.1.4.1.14988.1.1.3.15.0",
                            "1.3.6.1.4.1.14988.1.1.3.16.0",
                        )
                    ):
                        try:
                            val_int = int(val)
                            if val_int == 1:
                                sensor_value = "OK"
                                status = True
                            else:
                                sensor_value = "Sem Energia / Falha"
                                status = False
                        except (ValueError, TypeError):
                            sensor_value = val
                    elif "cpu" in (target.label or "").lower():
                        sensor_value = f"{val}%"
                    elif "uptime" in (target.label or "").lower():
                        try:
                            ticks = int(val)
                            total_seconds = ticks // 100
                            days = total_seconds // 86400
                            hours = (total_seconds % 86400) // 3600
                            minutes = (total_seconds % 3600) // 60
                            if days > 0:
                                sensor_value = f"{days}d {hours}h {minutes}m"
                            else:
                                sensor_value = f"{hours}h {minutes}m"
                        except ValueError:
                            sensor_value = val
                    else:
                        sensor_value = val
                else:
                    sensor_value = "Falha SNMP"

            elif sensor_type == "snmp_traffic":
                idx = target.sensor_identifier
                # Attempt to query 64-bit counters first, fallback to 32-bit counters
                # 64-bit: ifHCInOctets (1.3.6.1.2.1.31.1.1.1.6.<idx>), ifHCOutOctets (1.3.6.1.2.1.31.1.1.1.10.<idx>)
                # 32-bit: ifInOctets (1.3.6.1.2.1.2.2.1.10.<idx>), ifOutOctets (1.3.6.1.2.1.2.2.1.16.<idx>)
                oid_in_64 = f"1.3.6.1.2.1.31.1.1.1.6.{idx}"
                oid_out_64 = f"1.3.6.1.2.1.31.1.1.1.10.{idx}"

                status_in, val_in = PortCheckerService._snmp_get(
                    host, comm, snmp_port, oid_in_64
                )
                status_out, val_out = PortCheckerService._snmp_get(
                    host, comm, snmp_port, oid_out_64
                )

                if not status_in or val_in is None:
                    # Fallback to 32-bit
                    oid_in_32 = f"1.3.6.1.2.1.2.2.1.10.{idx}"
                    oid_out_32 = f"1.3.6.1.2.1.2.2.1.16.{idx}"
                    status_in, val_in = PortCheckerService._snmp_get(
                        host, comm, snmp_port, oid_in_32
                    )
                    status_out, val_out = PortCheckerService._snmp_get(
                        host, comm, snmp_port, oid_out_32
                    )

                latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

                if (
                    status_in
                    and val_in is not None
                    and status_out
                    and val_out is not None
                ):
                    try:
                        bytes_in = int(val_in)
                        bytes_out = int(val_out)
                        status = True

                        now = timezone.now()
                        old_val_val = target.last_counter_val
                        old_val_time = target.last_counter_time

                        # Save current counters as state
                        target.last_counter_val = bytes_in + bytes_out
                        target.last_counter_time = now
                        target.save(
                            update_fields=["last_counter_val", "last_counter_time"]
                        )

                        if old_val_val is not None and old_val_time is not None:
                            dt = (now - old_val_time).total_seconds()
                            if dt > 0:
                                delta_bytes = (bytes_in + bytes_out) - old_val_val
                                # Handle wrap-around gracefully
                                if delta_bytes < 0:
                                    delta_bytes = 0
                                bps = (delta_bytes * 8) / dt
                                sensor_value = PortCheckerService._format_bandwidth(bps)
                            else:
                                sensor_value = "Calculando..."
                        else:
                            sensor_value = "Aguardando 2ª leitura"
                    except ValueError:
                        status = False
                        sensor_value = "Erro de conversão"
                else:
                    status = False
                    sensor_value = "Falha SNMP"

        elif sensor_type == "mikrotik_api":
            if not device or not device.api_username:
                status = False
                sensor_value = "Credenciais ausentes"
            else:
                api = MikrotikAPI(
                    host, device.api_username, device.api_password, device.api_port
                )
                if api.connect():
                    status = True
                    metric = target.sensor_identifier
                    try:
                        if metric == "cpu":
                            res = api.talk(["/system/resource/print"])
                            cpu_load = "0"
                            for line in res:
                                for word in line:
                                    if word.startswith("=cpu-load="):
                                        cpu_load = word[10:]
                            sensor_value = f"{cpu_load}%"
                            val = cpu_load

                        elif metric.startswith("cpu:"):
                            core_idx = metric.split(":", 1)[1]
                            res = api.talk(["/system/resource/cpu/print"])
                            core_load = "0"
                            for line in res:
                                matched = False
                                for word in line:
                                    if word == f"=cpu={core_idx}":
                                        matched = True
                                if matched:
                                    for word in line:
                                        if word.startswith("=load="):
                                            core_load = word[6:]
                            sensor_value = f"{core_load}%"
                            val = core_load

                        elif metric == "temp":
                            res = api.talk(["/system/health/print"])
                            temp = "N/A"
                            for line in res:
                                for word in line:
                                    if word.startswith(
                                        "=temperature="
                                    ) or word.startswith("=cpu-temperature="):
                                        temp = word.split("=")[-1]
                            sensor_value = f"{temp}º C" if temp != "N/A" else "N/A"
                            val = temp if temp != "N/A" else None

                        elif metric.startswith("health:"):
                            health_name = metric.split(":", 1)[1]
                            res = api.talk(["/system/health/print"])
                            health_val = "N/A"
                            for line in res:
                                is_target = False
                                current_val = None
                                for word in line:
                                    if word == f"=name={health_name}":
                                        is_target = True
                                    elif word.startswith("=value="):
                                        current_val = word[7:]
                                if is_target and current_val is not None:
                                    health_val = current_val
                                    break

                                for word in line:
                                    if word.startswith(f"={health_name}="):
                                        health_val = word.split("=")[-1]

                            if not health_val or str(health_val).strip().lower() in (
                                "n/a",
                                "null",
                                "none",
                                "",
                            ):
                                status = False
                                sensor_value = "N/A"
                                val = None
                            else:
                                if "temp" in health_name.lower():
                                    sensor_value = f"{health_val}ºC"
                                elif "voltage" in health_name.lower():
                                    sensor_value = f"{health_val} V"
                                elif "current" in health_name.lower():
                                    sensor_value = f"{health_val} A"
                                elif "power" in health_name.lower():
                                    sensor_value = f"{health_val} W"
                                elif "fan" in health_name.lower():
                                    sensor_value = f"{health_val} RPM"
                                else:
                                    sensor_value = health_val
                                val = health_val

                        elif metric == "uptime":
                            res = api.talk(["/system/resource/print"])
                            uptime = "N/A"
                            for line in res:
                                for word in line:
                                    if word.startswith("=uptime="):
                                        uptime = word[8:]
                            sensor_value = uptime
                            val = uptime if uptime != "N/A" else None

                        elif metric.startswith("bgp:"):
                            peer_name = metric.split(":", 1)[1]
                            res = api.talk(
                                ["/routing/bgp/peer/print", f"?name={peer_name}"]
                            )
                            if not res or len(res) <= 1:
                                res = api.talk(
                                    ["/routing/bgp/session/print", f"?name={peer_name}"]
                                )

                            state = "N/A"
                            for line in res:
                                for word in line:
                                    if word.startswith("=state="):
                                        state = word[7:]

                            if state == "established":
                                status = True
                                sensor_value = "Estabelecido"
                            else:
                                status = False
                                sensor_value = (
                                    state.capitalize() if state != "N/A" else "Inativo"
                                )
                            val = state if state != "N/A" else None

                        elif metric.startswith("traffic:"):
                            interface_name = metric.split(":", 1)[1]
                            res = api.talk(
                                [
                                    "/interface/monitor-traffic",
                                    f"=interface={interface_name}",
                                    "=once=",
                                ]
                            )
                            rx_bps = 0.0
                            tx_bps = 0.0
                            for line in res:
                                for word in line:
                                    if word.startswith("=rx-bits-per-second="):
                                        rx_bps = float(word[20:])
                                    elif word.startswith("=tx-bits-per-second="):
                                        tx_bps = float(word[20:])
                            bps = rx_bps + tx_bps
                            sensor_value = PortCheckerService._format_bandwidth(bps)
                    except Exception as api_err:
                        status = False
                        sensor_value = f"Erro API: {str(api_err)}"
                    finally:
                        api.close()
                else:
                    status = False
                    sensor_value = "Erro de conexão API"
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # Logging and alerting logic (common to all sensors)
        old_status = target.last_status
        preceding_failures = 0
        downtime_duration_str = None
        if status and old_status is False:
            last_logs = target.logs.all().order_by("-timestamp")[:100]
            downtime_start = None
            for log in last_logs:
                if not log.status:
                    preceding_failures += 1
                    downtime_start = log.timestamp
                else:
                    break

            if downtime_start:
                duration = timezone.now() - downtime_start
                total_seconds = int(duration.total_seconds())
                if total_seconds < 60:
                    downtime_duration_str = f"{total_seconds}s"
                elif total_seconds < 3600:
                    downtime_duration_str = (
                        f"{total_seconds // 60}m {total_seconds % 60}s"
                    )
                else:
                    downtime_duration_str = (
                        f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"
                    )

        raw_bps = None
        if sensor_type == "snmp_traffic" and status and bps > 0:
            raw_bps = bps
        elif (
            sensor_type == "mikrotik_api"
            and target.sensor_identifier
            and target.sensor_identifier.startswith("traffic:")
            and status
            and bps > 0
        ):
            raw_bps = bps

        metric_val = PortCheckerService._parse_metric_value(
            sensor_type, target.label, val, raw_bps
        )

        try:
            with transaction.atomic():
                MonitorLog.objects.create(
                    target=target,
                    status=status,
                    latency=latency_ms,
                    metric_value=metric_val,
                )
                target.last_checked = timezone.now()
                target.last_status = status
                target.last_latency = latency_ms
                target.sensor_value = sensor_value
                target.save(
                    update_fields=[
                        "last_checked",
                        "last_status",
                        "last_latency",
                        "sensor_value",
                    ]
                )
        except Exception as e:
            logger.error("Erro ao salvar log de sensor %s: %s", target, str(e))
            return f"Error saving log: {str(e)}"

        # Trigger Telegram alert
        if target.telegram_alert_threshold > 0:
            should_alert = False

            if not status:
                consecutive_failures = 0
                last_logs = target.logs.all().order_by("-timestamp")[:5]
                for log in last_logs:
                    if not log.status:
                        consecutive_failures += 1
                    else:
                        break

                if consecutive_failures == target.telegram_alert_threshold:
                    should_alert = True
            else:
                if (
                    old_status is False
                    and preceding_failures >= target.telegram_alert_threshold
                ):
                    should_alert = True

            if should_alert:
                try:
                    from .utils import send_telegram_alert

                    send_telegram_alert(
                        target,
                        old_status,
                        status,
                        downtime_duration=downtime_duration_str,
                    )
                except Exception as tel_err:
                    logger.error(
                        "Falha ao invocar alerta do Telegram: %s", str(tel_err)
                    )

        status_str = "ONLINE" if status else "OFFLINE"
        return f"Sensor {target} -> {status_str} (Val: {sensor_value}, {latency_ms}ms)"


def log_audit(
    user: Any,
    action: str,
    model_name: str,
    object_repr: str,
    changes: Optional[str] = None,
) -> None:
    """Helper to log administrative actions to the AuditLog table."""
    try:
        user_val = user if (user and user.is_authenticated) else None
        AuditLog.objects.create(
            user=user_val,
            action=action,
            model_name=model_name,
            object_repr=object_repr,
            changes=changes,
        )
    except Exception as e:
        logger.error("Falha ao salvar log de auditoria: %s", str(e))


class TargetDetailService:
    """Service to calculate SLA metrics, downsample logs, and generate Chart.js contexts."""

    @staticmethod
    def get_chart_context(
        target: MonitorTarget, start_date_str: str, end_date_str: str, period: str
    ) -> dict:
        import datetime
        from datetime import timedelta

        from django.utils import timezone
        from django.utils.dateparse import parse_date

        now = timezone.now()
        start_date_val = None
        end_date_val = None

        if start_date_str and end_date_str:
            start_date_val = parse_date(start_date_str)
            end_date_val = parse_date(end_date_str)

        if start_date_val and end_date_val:
            start_dt = timezone.make_aware(
                datetime.datetime.combine(start_date_val, datetime.time.min)
            )
            end_dt = timezone.make_aware(
                datetime.datetime.combine(end_date_val, datetime.time.max)
            )
            period = "custom"
        else:
            if not period:
                period = "24h"
            if period == "7d":
                start_dt = now - timedelta(days=7)
                start_date_str = (now - timedelta(days=7)).strftime("%Y-%m-%d")
            elif period == "30d":
                start_dt = now - timedelta(days=30)
                start_date_str = (now - timedelta(days=30)).strftime("%Y-%m-%d")
            else:  # '24h'
                start_dt = now - timedelta(days=1)
                period = "24h"
                start_date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            end_dt = now
            end_date_str = now.strftime("%Y-%m-%d")

        # Filter logs in range
        chart_logs_query = target.logs.filter(
            timestamp__gte=start_dt, timestamp__lte=end_dt
        ).order_by("timestamp")

        # Downsample if log count exceeds 300 to optimize performance
        log_count = chart_logs_query.count()
        if log_count > 300:
            step = log_count // 300
            chart_logs = list(chart_logs_query)[::step]
        else:
            chart_logs = list(chart_logs_query)

        # Dynamic format for X-axis labels based on duration
        if (end_dt - start_dt) > timedelta(days=1):
            timestamp_format = "%d/%m %H:%M"
        else:
            timestamp_format = "%H:%M:%S"

        # Determine if we should plot metric_value instead of latency
        is_metric = target.sensor_type not in ("ping", "tcp")
        chart_latencies = []
        chart_timestamps_final = []
        chart_statuses_final = []
        for log in chart_logs:
            if not log.status:
                chart_timestamps_final.append(
                    timezone.localtime(log.timestamp).strftime(timestamp_format)
                )
                chart_latencies.append(0)
                chart_statuses_final.append(0)
            else:
                if is_metric:
                    if log.metric_value is not None:
                        chart_timestamps_final.append(
                            timezone.localtime(log.timestamp).strftime(timestamp_format)
                        )
                        chart_latencies.append(log.metric_value)
                        chart_statuses_final.append(1)
                    # Skip points with no metric_value (old logs before field was added)
                else:
                    chart_timestamps_final.append(
                        timezone.localtime(log.timestamp).strftime(timestamp_format)
                    )
                    chart_latencies.append(log.latency)
                    chart_statuses_final.append(1)

        return {
            "chart_timestamps": chart_timestamps_final,
            "chart_latencies": chart_latencies,
            "chart_statuses": chart_statuses_final,
            "period": period,
            "start_date": start_date_str,
            "end_date": end_date_str,
        }


class SLAReportService:
    """Service to compile SLA details, pairing targets, and generate PDF report data."""

    @staticmethod
    def generate_pdf_report(
        group: Group, start_date_str: str, end_date_str: str
    ) -> Tuple[Optional[bytes], Optional[str]]:
        import datetime
        import io
        from datetime import timedelta

        from django.db.models import Case, Count, F, FloatField, Q, Value, When
        from django.db.models.functions import Cast
        from django.template.loader import render_to_string
        from django.utils import timezone
        from django.utils.dateparse import parse_date
        from xhtml2pdf import pisa

        # Fallbacks
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30)

        if start_date_str:
            parsed_start = parse_date(start_date_str)
            if parsed_start:
                start_date = timezone.make_aware(
                    datetime.datetime.combine(parsed_start, datetime.time.min)
                )

        if end_date_str:
            parsed_end = parse_date(end_date_str)
            if parsed_end:
                end_date = timezone.make_aware(
                    datetime.datetime.combine(parsed_end, datetime.time.max)
                )

        # Calculate availability
        targets = (
            group.targets.filter(is_active=True)
            .annotate(
                total_logs=Count(
                    "logs",
                    filter=Q(
                        logs__timestamp__gte=start_date, logs__timestamp__lte=end_date
                    ),
                ),
                success_logs=Count(
                    "logs",
                    filter=Q(
                        logs__timestamp__gte=start_date,
                        logs__timestamp__lte=end_date,
                        logs__status=True,
                    ),
                ),
            )
            .annotate(
                availability=Case(
                    When(
                        total_logs=0,
                        then=Case(
                            When(last_status=True, then=Value(100.0)),
                            default=Value(0.0),
                        ),
                    ),
                    default=Cast(
                        F("success_logs") * 100.0 / F("total_logs"),
                        output_field=FloatField(),
                    ),
                )
            )
            .order_by("host", "port")
        )

        total_availability = 0.0
        target_count = len(targets)

        for target in targets:
            target.availability_rounded = round(target.availability, 1)
            total_availability += target.availability_rounded

        paired_targets = []
        cols = 2
        for i in range(0, len(targets), cols):
            chunk = list(targets[i : i + cols])
            while len(chunk) < cols:
                chunk.append(None)
            paired_targets.append(chunk)

        group_availability = (
            round(total_availability / target_count, 1) if target_count > 0 else 100.0
        )
        delta_days = (end_date - start_date).days
        if delta_days <= 0:
            delta_days = 1

        context = {
            "group": group,
            "paired_targets": paired_targets,
            "group_availability": group_availability,
            "target_count": target_count,
            "start_date": start_date,
            "end_date": end_date,
            "period_days": delta_days,
            "generated_at": timezone.now(),
        }

        html_string = render_to_string("monitor/group_report_pdf.html", context)
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer)

        if pisa_status.err:
            return None, None

        pdf_buffer.seek(0)
        return (
            pdf_buffer.getvalue(),
            f"relatorio_sla_{''.join([c if c.isalnum() else '_' for c in group.name.lower()])}.pdf",
        )


class GroupManagerService:
    """Service to encapsulate group settings update and targets bulk edits."""

    @staticmethod
    @transaction.atomic
    def update_group_settings(
        group: Group,
        new_name: str,
        check_interval_str: str,
        telegram_alert_threshold_str: str,
        selected_targets: List[str],
        user: Any,
    ) -> Tuple[bool, str]:
        old_name = group.name
        if old_name != new_name:
            group.name = new_name
            group.save()
            log_audit(
                user=user,
                action="Editar",
                model_name="Grupo",
                object_repr=old_name,
                changes=f"Grupo renomeado de '{old_name}' para '{new_name}'",
            )
        else:
            group.save()

        success_msg = (
            f"Grupo '{old_name}' renomeado para '{new_name}' com sucesso."
            if old_name != new_name
            else f"Grupo '{new_name}' atualizado."
        )

        if selected_targets:
            changes_desc = []
            if check_interval_str:
                try:
                    interval_val = int(check_interval_str)
                    updated_count = MonitorTarget.objects.filter(
                        id__in=selected_targets, group=group
                    ).update(check_interval=interval_val)
                    success_msg += f" Frequência de verificação atualizada em {updated_count} dispositivo(s)."
                    changes_desc.append(f"Frequência definida para {interval_val}m")
                except ValueError:
                    pass

            if telegram_alert_threshold_str:
                try:
                    threshold_val = int(telegram_alert_threshold_str)
                    updated_count = MonitorTarget.objects.filter(
                        id__in=selected_targets, group=group
                    ).update(telegram_alert_threshold=threshold_val)
                    success_msg += f" Regra de alerta do Telegram atualizada em {updated_count} dispositivo(s)."
                    changes_desc.append(
                        f"Alerta de Telegram definido para {threshold_val} falha(s)"
                    )
                except ValueError:
                    pass

            if changes_desc:
                log_audit(
                    user=user,
                    action="Lote",
                    model_name="Dispositivo",
                    object_repr=f"Grupo: {new_name}",
                    changes=f"Atualização em lote para {len(selected_targets)} dispositivos: {', '.join(changes_desc)}",
                )

        return True, success_msg


class DeviceDiscoveryService:
    """Service to handle device sensor auto-discovery via SNMP or MikroTik RouterOS API."""

    @staticmethod
    def _is_sensor_monitored(device: Device, sensor_type: str, identifier: str) -> bool:
        if not device or not device.pk:
            return False
        return device.sensors.filter(
            sensor_type=sensor_type, sensor_identifier=identifier
        ).exists()

    @staticmethod
    def discover_interfaces(device: Device) -> Tuple[List[dict], Optional[str]]:
        sensors = []
        error = None

        if device.device_type == "mikrotik":
            from .services import MikrotikAPI

            api = MikrotikAPI(
                device.host, device.api_username, device.api_password, device.api_port
            )
            if api.connect():
                try:
                    # 1. Discover Interfaces (Traffic)
                    try:
                        res = api.talk(["/interface/print"])
                        for line in res:
                            name = ""
                            for word in line:
                                if word.startswith("=name="):
                                    name = word[6:]
                            if name:
                                is_monitored = (
                                    DeviceDiscoveryService._is_sensor_monitored(
                                        device, "mikrotik_api", f"traffic:{name}"
                                    )
                                )
                                sensors.append(
                                    {
                                        "identifier": f"traffic:{name}",
                                        "name": name,
                                        "type_label": "Interface (Tráfego)",
                                        "is_monitored": is_monitored,
                                    }
                                )
                    except Exception as e:
                        logger.error("Erro ao descobrir interfaces via API: %s", str(e))

                    # 2. Discover CPU Cores
                    try:
                        res = api.talk(["/system/resource/cpu/print"])
                        for line in res:
                            cpu_idx = None
                            for word in line:
                                if word.startswith("=cpu="):
                                    cpu_idx = word[5:]
                            if cpu_idx is not None:
                                ident = f"cpu:{cpu_idx}"
                                is_monitored = (
                                    DeviceDiscoveryService._is_sensor_monitored(
                                        device, "mikrotik_api", ident
                                    )
                                )
                                sensors.append(
                                    {
                                        "identifier": ident,
                                        "name": f"CPU Core {cpu_idx}",
                                        "type_label": "CPU (Uso)",
                                        "is_monitored": is_monitored,
                                    }
                                )
                    except Exception as e:
                        logger.error("Erro ao descobrir CPUs via API: %s", str(e))

                    # 3. Discover Health Sensors
                    try:
                        res = api.talk(["/system/health/print"])
                        metrics = set()
                        for line in res:
                            for word in line:
                                if word.startswith("=name="):
                                    metrics.add(word[6:])
                                elif word.startswith("="):
                                    parts = word.split("=")
                                    if len(parts) >= 2:
                                        k = parts[1]
                                        if k not in (
                                            ".id",
                                            "value",
                                            "name",
                                            "re",
                                            "done",
                                            "trap",
                                            "fatal",
                                        ):
                                            metrics.add(k)

                        friendly_names = {
                            "voltage": "Voltagem",
                            "temperature": "Temperatura da Placa",
                            "cpu-temperature": "Temperatura da CPU",
                            "current": "Corrente",
                            "power-consumption": "Consumo de Energia",
                            "fan1-speed": "Cooler 1 Speed",
                            "fan2-speed": "Cooler 2 Speed",
                        }

                        for metric in sorted(metrics):
                            ident = f"health:{metric}"
                            is_monitored = DeviceDiscoveryService._is_sensor_monitored(
                                device, "mikrotik_api", ident
                            )
                            sensors.append(
                                {
                                    "identifier": ident,
                                    "name": friendly_names.get(
                                        metric, metric.replace("-", " ").capitalize()
                                    ),
                                    "type_label": "Saúde (Sensor)",
                                    "is_monitored": is_monitored,
                                }
                            )
                    except Exception as e:
                        logger.error(
                            "Erro ao descobrir sensores de saúde via API: %s", str(e)
                        )

                    # 4. Discover BGP Peers
                    try:
                        res = api.talk(["/routing/bgp/peer/print"])
                        found_bgp = False
                        for line in res:
                            peer_name = ""
                            peer_ip = ""
                            for word in line:
                                if word.startswith("=name="):
                                    peer_name = word[6:]
                                elif word.startswith("=remote-address="):
                                    peer_ip = word[16:]
                            if peer_name:
                                found_bgp = True
                                ident = f"bgp:{peer_name}"
                                is_monitored = (
                                    DeviceDiscoveryService._is_sensor_monitored(
                                        device, "mikrotik_api", ident
                                    )
                                )
                                sensors.append(
                                    {
                                        "identifier": ident,
                                        "name": f"BGP Peer: {peer_name} ({peer_ip or 'Endereço Indefinido'})",
                                        "type_label": "BGP (Sessão)",
                                        "is_monitored": is_monitored,
                                    }
                                )

                        if not found_bgp:
                            res = api.talk(["/routing/bgp/session/print"])
                            for line in res:
                                peer_name = ""
                                peer_ip = ""
                                for word in line:
                                    if word.startswith("=name="):
                                        peer_name = word[6:]
                                    elif word.startswith("=remote-address="):
                                        peer_ip = word[16:]
                                if peer_name:
                                    ident = f"bgp:{peer_name}"
                                    is_monitored = (
                                        DeviceDiscoveryService._is_sensor_monitored(
                                            device, "mikrotik_api", ident
                                        )
                                    )
                                    sensors.append(
                                        {
                                            "identifier": ident,
                                            "name": f"BGP Session: {peer_name} ({peer_ip or 'Endereço Indefinido'})",
                                            "type_label": "BGP (Sessão)",
                                            "is_monitored": is_monitored,
                                        }
                                    )
                    except Exception as e:
                        logger.error("Erro ao descobrir BGP via API: %s", str(e))

                except Exception as e:
                    error = f"Erro ao ler sensores via API: {str(e)}"
                finally:
                    api.close()
            else:
                error = "Não foi possível conectar na API MikroTik. Verifique as credenciais, porta e IP."

        elif device.device_type == "mikrotik_snmp":
            # 1. Discover Interfaces (Traffic)
            walk_results = PortCheckerService.snmp_walk(
                device.host,
                device.snmp_community,
                device.snmp_port,
                "1.3.6.1.2.1.2.2.1.2",
            )
            if walk_results is None:
                error = "Não foi possível obter dados via SNMP. Verifique o IP, Comunidade SNMP e a conectividade."
            else:
                for oid_str, val in walk_results:
                    idx = oid_str.split(".")[-1]
                    name = val
                    if any(
                        x in name.lower()
                        for x in [
                            "gpon",
                            "ether",
                            "sfp",
                            "port",
                            "pon",
                            "bridge",
                            "vlan",
                            "wlan",
                            "combo",
                            "ath",
                            "eth",
                            "br",
                            "lan",
                            "wan",
                        ]
                    ):
                        is_monitored = DeviceDiscoveryService._is_sensor_monitored(
                            device, "snmp_traffic", idx
                        )
                        sensors.append(
                            {
                                "identifier": f"snmp_traffic:{idx}",
                                "name": name,
                                "type_label": "Interface (Tráfego)",
                                "is_monitored": is_monitored,
                            }
                        )

                # 2. Discover CPU Cores
                cpu_results = PortCheckerService.snmp_walk(
                    device.host,
                    device.snmp_community,
                    device.snmp_port,
                    "1.3.6.1.2.1.25.3.3.1.2",
                )
                if cpu_results:
                    for oid_str, val in cpu_results:
                        idx = oid_str.split(".")[-1]
                        ident = f"snmp_numeric:1.3.6.1.2.1.25.3.3.1.2.{idx}"
                        is_monitored = DeviceDiscoveryService._is_sensor_monitored(
                            device, "snmp_numeric", f"1.3.6.1.2.1.25.3.3.1.2.{idx}"
                        )
                        sensors.append(
                            {
                                "identifier": ident,
                                "name": f"CPU Core {idx}",
                                "type_label": "CPU (Uso)",
                                "is_monitored": is_monitored,
                            }
                        )

                # 3. Discover Health Sensors via SNMP Walk
                health_results = PortCheckerService.snmp_walk(
                    device.host,
                    device.snmp_community,
                    device.snmp_port,
                    "1.3.6.1.4.1.14988.1.1.3",
                )
                if health_results:
                    health_map = {
                        "1.3.6.1.4.1.14988.1.1.3.8.0": "Voltagem",
                        "1.3.6.1.4.1.14988.1.1.3.9.0": "Temperatura da Placa",
                        "1.3.6.1.4.1.14988.1.1.3.10.0": "Temperatura da CPU",
                        "1.3.6.1.4.1.14988.1.1.3.11.0": "Temperatura da CPU",
                        "1.3.6.1.4.1.14988.1.1.3.12.0": "Consumo de Energia",
                        "1.3.6.1.4.1.14988.1.1.3.13.0": "Corrente",
                        "1.3.6.1.4.1.14988.1.1.3.14.0": "Consumo de Energia",
                        "1.3.6.1.4.1.14988.1.1.3.17.0": "Cooler 1 Speed",
                        "1.3.6.1.4.1.14988.1.1.3.18.0": "Cooler 2 Speed",
                    }
                    for oid_str, val in health_results:
                        val_str = str(val).strip()
                        if val_str and val_str.lower() not in (
                            "n/a",
                            "null",
                            "none",
                            "",
                            "nosuchinstance",
                            "nosuchobject",
                        ):
                            if oid_str in health_map:
                                label = health_map[oid_str]
                                ident = f"snmp_numeric:{oid_str}"
                                is_monitored = (
                                    DeviceDiscoveryService._is_sensor_monitored(
                                        device, "snmp_numeric", oid_str
                                    )
                                )
                                sensors.append(
                                    {
                                        "identifier": ident,
                                        "name": label,
                                        "type_label": "Saúde (Sensor)",
                                        "is_monitored": is_monitored,
                                    }
                                )

                # 4. Discover BGP Peers
                bgp_results = PortCheckerService.snmp_walk(
                    device.host,
                    device.snmp_community,
                    device.snmp_port,
                    "1.3.6.1.2.1.15.3.1.2",
                )
                if bgp_results:
                    for oid_str, val in bgp_results:
                        peer_ip = ".".join(oid_str.split(".")[-4:])
                        ident = f"snmp_numeric:1.3.6.1.2.1.15.3.1.2.{peer_ip}"
                        is_monitored = DeviceDiscoveryService._is_sensor_monitored(
                            device, "snmp_numeric", f"1.3.6.1.2.1.15.3.1.2.{peer_ip}"
                        )
                        sensors.append(
                            {
                                "identifier": ident,
                                "name": f"BGP Peer: {peer_ip}",
                                "type_label": "BGP (Sessão)",
                                "is_monitored": is_monitored,
                            }
                        )

        elif device.device_type in ("parks_olt", "generic_snmp"):
            walk_results = PortCheckerService.snmp_walk(
                device.host,
                device.snmp_community,
                device.snmp_port,
                "1.3.6.1.2.1.2.2.1.2",
            )
            if walk_results is None:
                error = "Não foi possível obter dados via SNMP. Verifique o IP, Comunidade SNMP e a conectividade."
            else:
                for oid_str, val in walk_results:
                    idx = oid_str.split(".")[-1]
                    name = val
                    if any(
                        x in name.lower()
                        for x in [
                            "gpon",
                            "ether",
                            "sfp",
                            "port",
                            "pon",
                            "bridge",
                            "vlan",
                            "wlan",
                            "combo",
                            "ath",
                            "eth",
                            "br",
                            "lan",
                            "wan",
                        ]
                    ):
                        is_monitored = DeviceDiscoveryService._is_sensor_monitored(
                            device, "snmp_traffic", idx
                        )
                        sensors.append(
                            {
                                "identifier": f"snmp_traffic:{idx}",
                                "name": name,
                                "type_label": "Interface (Tráfego)",
                                "is_monitored": is_monitored,
                            }
                        )
        else:
            error = "Auto-descoberta não suportada para este tipo de equipamento."
        return sensors, error

    @staticmethod
    def provision_sensors(device: Device, selected_identifiers: List[str]) -> int:
        created_count = 0

        for identifier in selected_identifiers:
            # 1. SNMP Traffic
            if identifier.startswith("snmp_traffic:"):
                idx = identifier.split(":", 1)[1]
                status, name = PortCheckerService._snmp_get(
                    device.host,
                    device.snmp_community,
                    device.snmp_port,
                    f"1.3.6.1.2.1.2.2.1.2.{idx}",
                )
                name = name or f"Interface {idx}"

                sensor, created = MonitorTarget.objects.get_or_create(
                    device=device,
                    sensor_type="snmp_traffic",
                    sensor_identifier=idx,
                    host=device.host,
                    group=device.group,
                    defaults={
                        "label": f"{device.name} - {name}",
                        "check_interval": device.check_interval,
                        "telegram_alert_threshold": device.telegram_alert_threshold,
                    },
                )
                if created:
                    created_count += 1
                    try:
                        PortCheckerService.check_target(sensor.id)
                    except Exception as e:
                        logger.error(
                            "Erro na checagem inicial síncrona da interface %d: %s",
                            sensor.id,
                            str(e),
                        )
                    try:
                        from .tasks import check_single_target

                        check_single_target.delay(sensor.id)
                    except Exception:
                        pass

            # 2. SNMP Numeric (CPU, Health, BGP)
            elif identifier.startswith("snmp_numeric:"):
                oid = identifier.split(":", 1)[1]

                # Determine friendly label and custom interval defaults based on OID
                label = f"Métrica {oid}"
                interval = device.check_interval

                health_labels = {
                    "1.3.6.1.4.1.14988.1.1.3.8.0": "Voltagem",
                    "1.3.6.1.4.1.14988.1.1.3.9.0": "Temperatura da Placa",
                    "1.3.6.1.4.1.14988.1.1.3.10.0": "Temperatura da CPU",
                    "1.3.6.1.4.1.14988.1.1.3.11.0": "Temperatura da CPU",
                    "1.3.6.1.4.1.14988.1.1.3.12.0": "Consumo de Energia",
                    "1.3.6.1.4.1.14988.1.1.3.13.0": "Corrente",
                    "1.3.6.1.4.1.14988.1.1.3.14.0": "Consumo de Energia",
                    "1.3.6.1.4.1.14988.1.1.3.15.0": "Estado da PSU 1",
                    "1.3.6.1.4.1.14988.1.1.3.16.0": "Estado da PSU 2",
                    "1.3.6.1.4.1.14988.1.1.3.17.0": "Cooler 1 Speed",
                    "1.3.6.1.4.1.14988.1.1.3.18.0": "Cooler 2 Speed",
                }

                if oid in health_labels:
                    label = health_labels[oid]
                    interval = max(5, device.check_interval)
                elif oid.startswith("1.3.6.1.2.1.25.3.3.1.2."):
                    idx = oid.split(".")[-1]
                    label = f"CPU Core {idx}"
                elif oid.startswith("1.3.6.1.2.1.15.3.1.2."):
                    peer_ip = ".".join(oid.split(".")[-4:])
                    label = f"BGP Peer {peer_ip}"

                sensor, created = MonitorTarget.objects.get_or_create(
                    device=device,
                    sensor_type="snmp_numeric",
                    sensor_identifier=oid,
                    host=device.host,
                    group=device.group,
                    defaults={
                        "label": f"{device.name} - {label}",
                        "check_interval": interval,
                        "telegram_alert_threshold": device.telegram_alert_threshold,
                    },
                )
                if created:
                    created_count += 1
                    try:
                        PortCheckerService.check_target(sensor.id)
                    except Exception as e:
                        logger.error(
                            "Erro na checagem inicial síncrona do sensor SNMP %d: %s",
                            sensor.id,
                            str(e),
                        )
                    try:
                        from .tasks import check_single_target

                        check_single_target.delay(sensor.id)
                    except Exception:
                        pass

            # 3. MikroTik API (Traffic, CPU, Health, BGP)
            elif device.device_type == "mikrotik":
                # Map metric identifiers to labels
                label = identifier
                interval = device.check_interval

                if identifier.startswith("traffic:"):
                    name = identifier.split(":", 1)[1]
                    label = f"{name} Tráfego"
                elif identifier.startswith("cpu:"):
                    cpu_idx = identifier.split(":", 1)[1]
                    label = f"CPU Core {cpu_idx}"
                elif identifier.startswith("health:"):
                    metric = identifier.split(":", 1)[1]
                    friendly_names = {
                        "voltage": "Voltagem",
                        "temperature": "Temperatura da Placa",
                        "cpu-temperature": "Temperatura da CPU",
                        "current": "Corrente",
                        "power-consumption": "Consumo de Energia",
                        "fan1-speed": "Cooler 1 Speed",
                        "fan2-speed": "Cooler 2 Speed",
                    }
                    label = friendly_names.get(
                        metric, metric.replace("-", " ").capitalize()
                    )
                    interval = max(5, device.check_interval)
                elif identifier.startswith("bgp:"):
                    peer_name = identifier.split(":", 1)[1]
                    label = f"BGP Peer {peer_name}"

                sensor, created = MonitorTarget.objects.get_or_create(
                    device=device,
                    sensor_type="mikrotik_api",
                    sensor_identifier=identifier,
                    host=device.host,
                    group=device.group,
                    defaults={
                        "label": f"{device.name} - {label}",
                        "check_interval": interval,
                        "telegram_alert_threshold": device.telegram_alert_threshold,
                    },
                )
                if created:
                    created_count += 1
                    try:
                        PortCheckerService.check_target(sensor.id)
                    except Exception as e:
                        logger.error(
                            "Erro na checagem inicial síncrona do sensor API %d: %s",
                            sensor.id,
                            str(e),
                        )
                    try:
                        from .tasks import check_single_target

                        check_single_target.delay(sensor.id)
                    except Exception:
                        pass

        return created_count


class DashboardService:
    @staticmethod
    def get_filtered_queryset(search_query: str, status_filter: str, group_id: str):
        queryset = MonitorTarget.objects.select_related("group").filter(
            device__isnull=True
        )
        if search_query:
            queryset = queryset.filter(
                Q(host__icontains=search_query)
                | Q(label__icontains=search_query)
                | Q(port__icontains=search_query)
                | Q(group__name__icontains=search_query)
            )
        if status_filter == "online":
            queryset = queryset.filter(last_status=True, is_active=True)
        elif status_filter == "offline":
            queryset = queryset.filter(last_status=False, is_active=True)
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        return queryset

    @staticmethod
    def get_dashboard_stats(group_id: str = ""):
        all_targets = MonitorTarget.objects.filter(device__isnull=True)
        if group_id:
            all_targets = all_targets.filter(group_id=group_id)
        return {
            "total_count": all_targets.count(),
            "online_count": all_targets.filter(
                last_status=True, is_active=True
            ).count(),
            "offline_count": all_targets.filter(
                last_status=False, is_active=True
            ).count(),
            "inactive_count": all_targets.filter(is_active=False).count(),
        }

    @staticmethod
    def auto_correct_sensor_labels():
        health_labels = {
            "1.3.6.1.4.1.14988.1.1.3.8.0": "Voltagem",
            "1.3.6.1.4.1.14988.1.1.3.9.0": "Temperatura da Placa",
            "1.3.6.1.4.1.14988.1.1.3.10.0": "Temperatura da CPU",
            "1.3.6.1.4.1.14988.1.1.3.11.0": "Temperatura da CPU",
            "1.3.6.1.4.1.14988.1.1.3.12.0": "Consumo de Energia",
            "1.3.6.1.4.1.14988.1.1.3.13.0": "Corrente",
            "1.3.6.1.4.1.14988.1.1.3.14.0": "Consumo de Energia",
            "1.3.6.1.4.1.14988.1.1.3.15.0": "Estado da PSU 1",
            "1.3.6.1.4.1.14988.1.1.3.16.0": "Estado da PSU 2",
            "1.3.6.1.4.1.14988.1.1.3.17.0": "Cooler 1 Speed",
            "1.3.6.1.4.1.14988.1.1.3.18.0": "Cooler 2 Speed",
        }
        for sensor in MonitorTarget.objects.filter(
            sensor_type="snmp_numeric",
            label__icontains="métrica 1.3.6.1.4.1.14988.1.1.3.",
        ):
            oid_key = sensor.sensor_identifier
            if oid_key in health_labels:
                device_prefix = f"{sensor.device.name} - " if sensor.device else ""
                sensor.label = f"{device_prefix}{health_labels[oid_key]}"
                sensor.save(update_fields=["label"])


class TargetService:
    @staticmethod
    def auto_correct_target_label(target: MonitorTarget):
        if "métrica 1.3.6.1.4.1.14988.1.1.3." in (target.label or "").lower():
            oid_key = target.sensor_identifier
            health_labels = {
                "1.3.6.1.4.1.14988.1.1.3.8.0": "Voltagem",
                "1.3.6.1.4.1.14988.1.1.3.9.0": "Temperatura da Placa",
                "1.3.6.1.4.1.14988.1.1.3.10.0": "Temperatura da CPU",
                "1.3.6.1.4.1.14988.1.1.3.11.0": "Temperatura da CPU",
                "1.3.6.1.4.1.14988.1.1.3.12.0": "Consumo de Energia",
                "1.3.6.1.4.1.14988.1.1.3.13.0": "Corrente",
                "1.3.6.1.4.1.14988.1.1.3.14.0": "Consumo de Energia",
                "1.3.6.1.4.1.14988.1.1.3.15.0": "Estado da PSU 1",
                "1.3.6.1.4.1.14988.1.1.3.16.0": "Estado da PSU 2",
                "1.3.6.1.4.1.14988.1.1.3.17.0": "Cooler 1 Speed",
                "1.3.6.1.4.1.14988.1.1.3.18.0": "Cooler 2 Speed",
            }
            if oid_key in health_labels:
                device_prefix = f"{target.device.name} - " if target.device else ""
                target.label = f"{device_prefix}{health_labels[oid_key]}"
                target.save(update_fields=["label"])

    @staticmethod
    def toggle_target(target: MonitorTarget, user: Any) -> Tuple[bool, str]:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        status_label = "ativado" if target.is_active else "desativado"
        log_audit(
            user=user,
            action="Ativar" if target.is_active else "Desativar",
            model_name="Dispositivo",
            object_repr=f"{target.label or target.host}:{target.port}",
            changes=f"Alterado estado de atividade para: {status_label.capitalize()}",
        )
        return target.is_active, f"O monitoramento do alvo foi {status_label}."

    @staticmethod
    def delete_target(target: MonitorTarget, user: Any) -> str:
        host_port = f"{target.host}:{target.port}"
        label_repr = f"{target.label or target.host}:{target.port}"
        group_name = target.group.name if target.group else "Nenhum"
        target.delete()
        log_audit(
            user=user,
            action="Excluir",
            model_name="Dispositivo",
            object_repr=label_repr,
            changes=f"Excluído monitoramento de {host_port}. Grupo: {group_name}",
        )
        return host_port

    @staticmethod
    def trigger_manual_check(target_id: Optional[int]):
        from .tasks import check_all_targets, check_single_target

        if target_id:
            try:
                PortCheckerService.check_target(target_id)
            except Exception as e:
                logger.error(
                    "Erro na checagem síncrona manual do alvo %d: %s", target_id, str(e)
                )
            try:
                check_single_target.delay(target_id)
            except Exception:
                pass
        else:
            active_targets = MonitorTarget.objects.filter(is_active=True)
            for t in active_targets:
                try:
                    PortCheckerService.check_target(t.id)
                except Exception as e:
                    logger.error(
                        "Erro na checagem síncrona manual global do alvo %d: %s",
                        t.id,
                        str(e),
                    )
            try:
                check_all_targets.delay()
            except Exception:
                pass

    @staticmethod
    def update_target(
        target: MonitorTarget, cleaned_data: dict, user: Any
    ) -> Tuple[MonitorTarget, List[str]]:
        old_host = target.host
        old_port = target.port
        old_label = target.label
        old_interval = target.check_interval
        old_threshold = target.telegram_alert_threshold
        old_group = target.group.name if target.group else "Nenhum"

        for field, value in cleaned_data.items():
            setattr(target, field, value)
        target.save()

        target.refresh_from_db()
        new_host = target.host
        new_port = target.port
        new_label = target.label
        new_interval = target.check_interval
        new_threshold = target.telegram_alert_threshold
        new_group = target.group.name if target.group else "Nenhum"

        changes = []
        if old_host != new_host:
            changes.append(f"IP: {old_host} -> {new_host}")
        if old_port != new_port:
            changes.append(f"Porta: {old_port} -> {new_port}")
        if old_label != new_label:
            changes.append(
                f"Nome/Rótulo: {old_label or 'Vazio'} -> {new_label or 'Vazio'}"
            )
        if old_interval != new_interval:
            changes.append(f"Frequência: {old_interval}m -> {new_interval}m")
        if old_threshold != new_threshold:
            changes.append(
                f"Regra de Alerta: {old_threshold} falha(s) -> {new_threshold} falha(s)"
            )
        if old_group != new_group:
            changes.append(f"Grupo: {old_group} -> {new_group}")

        if changes:
            log_audit(
                user=user,
                action="Editar",
                model_name="Dispositivo",
                object_repr=f"{target.label or target.host}:{target.port}",
                changes="Alterações: " + ", ".join(changes),
            )
        return target, changes


class GroupService:
    @staticmethod
    def create_group(name: str, user: Any) -> Tuple[Group, bool]:
        group, created = Group.objects.get_or_create(name=name)
        if created:
            log_audit(
                user=user,
                action="Criar",
                model_name="Grupo",
                object_repr=name,
                changes=f"Novo grupo '{name}' cadastrado",
            )
        return group, created

    @staticmethod
    def delete_group(group: Group, user: Any):
        group_name = group.name
        group.delete()
        log_audit(
            user=user,
            action="Excluir",
            model_name="Grupo",
            object_repr=group_name,
            changes=f"Grupo '{group_name}' excluído (dispositivos foram desassociados)",
        )


class DeviceService:
    @staticmethod
    def create_device_with_sensors(
        device: Device, selected: List[str], discovery_run: bool, user: Any
    ) -> Tuple[int, bool]:
        if discovery_run:
            created_count = DeviceDiscoveryService.provision_sensors(device, selected)
            log_audit(
                user=user,
                action="Criar",
                model_name="Equipamento",
                object_repr=device.name,
                changes=f"Novo equipamento cadastrado com auto-descoberta na criação. Nome: {device.name}, Tipo: {device.device_type}, IP: {device.host}. Sensores: {', '.join(selected)}",
            )
            return created_count, True

        SENSOR_DEFS = {
            "generic_ping": {
                "ping": {
                    "type": "ping",
                    "identifier": "",
                    "label": "Ping",
                    "interval": 1,
                },
            },
            "mikrotik": {
                "ping": {
                    "type": "ping",
                    "identifier": "",
                    "label": "Ping",
                    "interval": 1,
                },
                "cpu": {
                    "type": "mikrotik_api",
                    "identifier": "cpu",
                    "label": "CPU",
                    "interval": 1,
                },
                "temp": {
                    "type": "mikrotik_api",
                    "identifier": "temp",
                    "label": "Temp",
                    "interval": 5,
                },
                "uptime": {
                    "type": "mikrotik_api",
                    "identifier": "uptime",
                    "label": "Uptime",
                    "interval": 15,
                },
            },
            "mikrotik_snmp": {
                "ping": {
                    "type": "ping",
                    "identifier": "",
                    "label": "Ping",
                    "interval": 1,
                },
                "cpu": {
                    "type": "snmp_numeric",
                    "identifier": "1.3.6.1.2.1.25.3.3.1.2.1",
                    "label": "CPU",
                    "interval": 1,
                },
                "temp": {
                    "type": "snmp_numeric",
                    "identifier": "1.3.6.1.4.1.14988.1.1.3.10.0",
                    "label": "Temp CPU",
                    "interval": 5,
                },
                "uptime": {
                    "type": "snmp_numeric",
                    "identifier": "1.3.6.1.2.1.1.3.0",
                    "label": "Uptime",
                    "interval": 15,
                },
            },
            "parks_olt": {
                "ping": {
                    "type": "ping",
                    "identifier": "",
                    "label": "Ping",
                    "interval": 1,
                },
            },
            "generic_snmp": {
                "ping": {
                    "type": "ping",
                    "identifier": "",
                    "label": "Ping",
                    "interval": 1,
                },
            },
        }

        defs = SENSOR_DEFS.get(device.device_type, {})
        if not selected:
            selected = list(defs.keys())

        created_count = 0
        for key in selected:
            if key not in defs:
                continue
            s = defs[key]
            kwargs = dict(
                device=device,
                sensor_type=s["type"],
                host=device.host,
                group=device.group,
            )
            if s["identifier"]:
                kwargs["sensor_identifier"] = s["identifier"]

            interval = device.check_interval
            if s["type"] == "snmp_numeric" and "temp" in s["label"].lower():
                interval = max(5, device.check_interval)
            elif s["type"] == "mikrotik_api" and s["identifier"] == "temp":
                interval = max(5, device.check_interval)

            t, created = MonitorTarget.objects.get_or_create(
                **kwargs,
                defaults={
                    "label": f"{device.name} - {s['label']}",
                    "check_interval": interval,
                    "telegram_alert_threshold": device.telegram_alert_threshold,
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
            elif not t.is_active:
                t.is_active = True
                t.save(update_fields=["is_active"])

        log_audit(
            user=user,
            action="Criar",
            model_name="Equipamento",
            object_repr=device.name,
            changes=f"Novo equipamento cadastrado. Nome: {device.name}, Tipo: {device.device_type}, IP: {device.host}. Sensores: {', '.join(selected)}",
        )
        return created_count, False

    @staticmethod
    def update_device_settings_and_sensors(
        device: Device, selected_identifiers: List[str], user: Any
    ):
        device.sensors.all().update(
            telegram_alert_threshold=device.telegram_alert_threshold, group=device.group
        )

        for sensor in device.sensors.all():
            interval = device.check_interval
            if (
                sensor.sensor_type == "snmp_numeric"
                and "temp" in (sensor.label or "").lower()
            ):
                interval = max(5, device.check_interval)
            elif (
                sensor.sensor_type == "mikrotik_api"
                and sensor.sensor_identifier.startswith("health:temp")
            ):
                interval = max(5, device.check_interval)
            sensor.check_interval = interval
            sensor.save(update_fields=["check_interval"])

        def get_target_identifier(target):
            if target.sensor_type == "ping":
                return "ping"
            elif target.sensor_type == "snmp_traffic":
                return f"snmp_traffic:{target.sensor_identifier}"
            elif target.sensor_type == "snmp_numeric":
                return f"snmp_numeric:{target.sensor_identifier}"
            elif target.sensor_type == "mikrotik_api":
                return target.sensor_identifier
            return None

        existing_targets = device.sensors.all()
        for target in existing_targets:
            ident = get_target_identifier(target)
            if ident and ident not in selected_identifiers:
                target.delete()

        existing_idents = {get_target_identifier(t) for t in existing_targets}
        new_idents = [
            ident
            for ident in selected_identifiers
            if ident and ident not in existing_idents
        ]
        if new_idents:
            DeviceDiscoveryService.provision_sensors(device, new_idents)

        log_audit(
            user=user,
            action="Editar",
            model_name="Equipamento",
            object_repr=device.name,
            changes=f"Alterado configurações do equipamento ID {device.id} (propagado para {device.sensors.count()} sensores)",
        )

    @staticmethod
    def delete_device(device: Device, user: Any):
        device_name = device.name
        device.delete()
        log_audit(
            user=user,
            action="Excluir",
            model_name="Equipamento",
            object_repr=device_name,
            changes=f"Equipamento '{device_name}' e seus sensores foram excluídos",
        )


class TelegramService:
    @staticmethod
    def send_test_message(target: MonitorTarget) -> Tuple[bool, Optional[str]]:
        from django.conf import settings
        from django.utils import timezone

        from .utils import send_telegram_message

        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        chat_id = getattr(settings, "TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return False, "Telegram não está configurado no arquivo .env."

        label = target.label or "Sem identificação"
        group_name = target.group.name if target.group else "Sem grupo"
        local_time = timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M:%S")
        message = (
            f"<b>Teste de Notificação Manual</b>\n\n"
            f"<b>Dispositivo:</b> {label}\n"
            f"<b>IP:</b> <code>{target.host}</code>\n"
            f"<b>Porta:</b> <code>{target.port}</code>\n"
            f"<b>Grupo:</b> {group_name}\n"
            f"<b>Horário:</b> {local_time}"
        )
        success = send_telegram_message(message)
        return success, (
            None
            if success
            else "Falha ao entregar a mensagem no Telegram. Verifique o Token e o Chat ID."
        )
