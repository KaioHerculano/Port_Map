import re
import socket
import time
import logging
from typing import List, Tuple, Optional
from django.db import transaction
from django.utils import timezone
from .models import MonitorTarget, MonitorLog, Group

logger = logging.getLogger(__name__)


class PortParserService:
    """Service to parse and create monitor targets from raw text lines."""

    @staticmethod
    @transaction.atomic
    def parse_and_create_targets(text: str, group: Optional[Group] = None, check_interval: int = 60, telegram_alert_threshold: int = 1) -> Tuple[List[MonitorTarget], List[str]]:
        lines = text.strip().split('\n')
        created_targets: List[MonitorTarget] = []
        errors: List[str] = []

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            # Parse optional label in brackets [] or parens ()
            label: Optional[str] = None
            label_match = re.search(r'(?:\[|\()(.*?)(?:\]|\))', line)
            if label_match:
                label = label_match.group(1).strip()
                line = re.sub(r'(?:\[|\()(.*?)(?:\]|\))', '', line).strip()

            if ':' not in line:
                errors.append(f"Linha {line_num}: Formato inválido. Sem caractere ':'. Ex: '192.168.1.1:80'")
                continue

            host, ports_str = line.rsplit(':', 1)
            host = host.strip()
            ports_str = ports_str.strip()

            if not host:
                errors.append(f"Linha {line_num}: Host/IP vazio.")
                continue

            # Parse ports
            port_parts = ports_str.split(',')
            ports: List[int] = []
            line_skipped = False
            
            max_ports_per_line = 100
            max_total_targets = 500

            for part in port_parts:
                part = part.strip()
                if '-' in part:
                    try:
                        start_p_str, end_p_str = part.split('-')
                        start_p = int(start_p_str.strip())
                        end_p = int(end_p_str.strip())
                        if start_p > end_p:
                            start_p, end_p = end_p, start_p
                        
                        # Validate range boundaries first
                        if not (1 <= start_p <= 65535) or not (1 <= end_p <= 65535):
                            errors.append(f"Linha {line_num}: Faixa de portas fora do limite 1-65535: '{part}'")
                            line_skipped = True
                            break
                        
                        # Limit range size to prevent abuse/errors
                        range_size = end_p - start_p + 1
                        if range_size > max_ports_per_line:
                            errors.append(f"Linha {line_num}: Faixa de portas muito grande ({range_size} portas). O limite máximo por faixa é {max_ports_per_line}.")
                            line_skipped = True
                            break
                            
                        ports.extend(range(start_p, end_p + 1))
                    except ValueError:
                        errors.append(f"Linha {line_num}: Faixa de portas inválida '{part}'")
                        line_skipped = True
                        break
                else:
                    try:
                        port = int(part)
                        if not (1 <= port <= 65535):
                            errors.append(f"Linha {line_num}: Porta fora do limite 1-65535: '{port}'")
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
                errors.append(f"Linha {line_num}: Quantidade de portas ({len(ports)}) excede o limite máximo de {max_ports_per_line} por linha.")
                continue

            # Check if adding these ports exceeds total limit
            if len(created_targets) + len(ports) > max_total_targets:
                errors.append(f"Limite máximo de {max_total_targets} alvos por importação atingido. Importação interrompida.")
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
                            'group': line_group, 
                            'label': label, 
                            'is_active': True,
                            'check_interval': check_interval,
                            'telegram_alert_threshold': telegram_alert_threshold
                        }
                    )
                    created_targets.append(target)
                except Exception as e:
                    logger.error("Erro ao salvar alvo %s:%d: %s", host, port, str(e))
                    errors.append(f"Linha {line_num}: Erro ao salvar alvo no banco de dados.")

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
        import hashlib
        import binascii
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
                hasher.update(b'\x00')
                hasher.update(self.password.encode('utf-8'))
                hasher.update(binascii.unhexlify(token))
                hashed_pass = binascii.hexlify(hasher.digest()).decode('utf-8')
                
                self.write_sentence(["/login", f"=name={self.username}", f"=response=00{hashed_pass}"])
                response = self.read_sentence()
                for word in response:
                    if word.startswith("!trap"):
                        return False
            else:
                # Modern plain password flow (ROS >= 6.43)
                self.write_sentence(["/login", f"=name={self.username}", f"=password={self.password}"])
                response = self.read_sentence()
                for word in response:
                    if word.startswith("!trap") or word.startswith("!fatal"):
                        return False
            return True
        except Exception as e:
            logger.error("MikroTik API connect failure: %s", str(e))
            return False

    def write_word(self, word: str):
        b_word = word.encode('utf-8')
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
            header = bytes([0xF0, (length >> 24) & 0xFF, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        self.sock.sendall(header + b_word)

    def write_sentence(self, sentence: List[str]):
        for word in sentence:
            self.write_word(word)
        self.sock.sendall(b'\x00')

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
        return data.decode('utf-8', errors='ignore')

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
        import subprocess
        import sys
        import re
        
        is_windows = sys.platform.lower().startswith('win')
        if is_windows:
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]
            
        start = time.perf_counter()
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout + 1)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            
            if res.returncode == 0:
                time_match = re.search(r'(?:time|tempo)[=<]\s*([\d\.]+)\s*ms', res.stdout, re.IGNORECASE)
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
    def _snmp_get(host: str, community: str, port: int, oid: str, timeout: float = 2.0) -> Tuple[bool, Optional[str]]:
        from pysnmp.hlapi import getCmd, CommunityData, UdpTransportTarget, ContextData, ObjectType, ObjectIdentity
        try:
            iterator = getCmd(
                next(iter([])),
                CommunityData(community, mpModel=1),
                UdpTransportTarget((host, port), timeout=timeout, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(oid))
            )
            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
            
            if errorIndication or errorStatus:
                return False, None
            
            if varBinds:
                return True, str(varBinds[0][1])
        except Exception as e:
            logger.error("SNMP get failed for %s (%s): %s", host, oid, str(e))
        return False, None

    @staticmethod
    def _format_bandwidth(bps: float) -> str:
        if bps < 1000:
            return f"{bps:.2f} bps"
        elif bps < 1000000:
            return f"{bps/1000:.2f} kbps"
        elif bps < 1000000000:
            return f"{bps/1000000:.2f} mbps"
        else:
            return f"{bps/1000000000:.2f} gbps"

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

        start_time = time.perf_counter()

        # Probing based on sensor_type
        if sensor_type == 'tcp':
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

        elif sensor_type == 'ping':
            status, latency_ms = PortCheckerService._ping(host)
            sensor_value = f"{latency_ms} ms / 0% perda" if status else "Sem resposta"

        elif sensor_type in ('snmp_numeric', 'snmp_traffic'):
            comm = device.snmp_community if device else "public"
            snmp_port = device.snmp_port if device else 161
            
            if sensor_type == 'snmp_numeric':
                oid = target.sensor_identifier
                status, val = PortCheckerService._snmp_get(host, comm, snmp_port, oid)
                latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
                if status and val is not None:
                    # Specific formatting for CPU or Temp if labels match
                    if "temp" in (target.label or "").lower() or "temperatura" in (target.label or "").lower():
                        sensor_value = f"{val}º C"
                    elif "cpu" in (target.label or "").lower():
                        sensor_value = f"{val}%"
                    else:
                        sensor_value = val
                else:
                    sensor_value = "Falha SNMP"
            
            elif sensor_type == 'snmp_traffic':
                idx = target.sensor_identifier
                # Attempt to query 64-bit counters first, fallback to 32-bit counters
                # 64-bit: ifHCInOctets (1.3.6.1.2.1.31.1.1.1.6.<idx>), ifHCOutOctets (1.3.6.1.2.1.31.1.1.1.10.<idx>)
                # 32-bit: ifInOctets (1.3.6.1.2.1.2.2.1.10.<idx>), ifOutOctets (1.3.6.1.2.1.2.2.1.16.<idx>)
                oid_in_64 = f"1.3.6.1.2.1.31.1.1.1.6.{idx}"
                oid_out_64 = f"1.3.6.1.2.1.31.1.1.1.10.{idx}"
                
                status_in, val_in = PortCheckerService._snmp_get(host, comm, snmp_port, oid_in_64)
                status_out, val_out = PortCheckerService._snmp_get(host, comm, snmp_port, oid_out_64)
                
                if not status_in or val_in is None:
                    # Fallback to 32-bit
                    oid_in_32 = f"1.3.6.1.2.1.2.2.1.10.{idx}"
                    oid_out_32 = f"1.3.6.1.2.1.2.2.1.16.{idx}"
                    status_in, val_in = PortCheckerService._snmp_get(host, comm, snmp_port, oid_in_32)
                    status_out, val_out = PortCheckerService._snmp_get(host, comm, snmp_port, oid_out_32)

                latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
                
                if status_in and val_in is not None and status_out and val_out is not None:
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
                        target.save(update_fields=['last_counter_val', 'last_counter_time'])
                        
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

        elif sensor_type == 'mikrotik_api':
            if not device or not device.api_username:
                status = False
                sensor_value = "Credenciais ausentes"
            else:
                api = MikrotikAPI(host, device.api_username, device.api_password, device.api_port)
                if api.connect():
                    status = True
                    metric = target.sensor_identifier
                    try:
                        if metric == 'cpu':
                            res = api.talk(["/system/resource/print"])
                            cpu_load = "0"
                            for line in res:
                                for word in line:
                                    if word.startswith("=cpu-load="):
                                        cpu_load = word[10:]
                            sensor_value = f"{cpu_load}%"
                            
                        elif metric == 'temp':
                            # Try print health, fallback if temperature is not present
                            res = api.talk(["/system/health/print"])
                            temp = "N/A"
                            for line in res:
                                for word in line:
                                    if word.startswith("=temperature=") or word.startswith("=cpu-temperature="):
                                        temp = word.split("=")[-1]
                            sensor_value = f"{temp}º C" if temp != "N/A" else "N/A"

                        elif metric == 'uptime':
                            res = api.talk(["/system/resource/print"])
                            uptime = "N/A"
                            for line in res:
                                for word in line:
                                    if word.startswith("=uptime="):
                                        uptime = word[8:]
                            sensor_value = uptime

                        elif metric.startswith('traffic:'):
                            interface_name = metric.split(':', 1)[1]
                            res = api.talk(["/interface/monitor-traffic", f"=interface={interface_name}", "=once="])
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
        if status and old_status == False:
            last_logs = target.logs.all().order_by('-timestamp')[:100]
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
                    downtime_duration_str = f"{total_seconds // 60}m {total_seconds % 60}s"
                else:
                    downtime_duration_str = f"{total_seconds // 3600}h {(total_seconds % 3600) // 60}m"

        try:
            with transaction.atomic():
                MonitorLog.objects.create(
                    target=target,
                    status=status,
                    latency=latency_ms
                )
                target.last_checked = timezone.now()
                target.last_status = status
                target.last_latency = latency_ms
                target.sensor_value = sensor_value
                target.save(update_fields=['last_checked', 'last_status', 'last_latency', 'sensor_value'])
        except Exception as e:
            logger.error("Erro ao salvar log de sensor %s: %s", target, str(e))
            return f"Error saving log: {str(e)}"

        # Trigger Telegram alert
        if target.telegram_alert_threshold > 0:
            should_alert = False
            
            if not status:
                consecutive_failures = 0
                last_logs = target.logs.all().order_by('-timestamp')[:5]
                for log in last_logs:
                    if not log.status:
                        consecutive_failures += 1
                    else:
                        break
                
                if consecutive_failures == target.telegram_alert_threshold:
                    should_alert = True
            else:
                if old_status == False and preceding_failures >= target.telegram_alert_threshold:
                    should_alert = True
                    
            if should_alert:
                try:
                    from .utils import send_telegram_alert
                    send_telegram_alert(target, old_status, status, downtime_duration=downtime_duration_str)
                except Exception as tel_err:
                    logger.error("Falha ao invocar alerta do Telegram: %s", str(tel_err))

        status_str = "ONLINE" if status else "OFFLINE"
        return f"Sensor {target} -> {status_str} (Val: {sensor_value}, {latency_ms}ms)"
