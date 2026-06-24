import re
import socket
import time
import logging
from typing import List, Tuple, Optional
from django.db import transaction
from django.utils import timezone
from .models import MonitorTarget, MonitorLog

logger = logging.getLogger(__name__)


class PortParserService:
    """Service to parse and create monitor targets from raw text lines."""

    @staticmethod
    @transaction.atomic
    def parse_and_create_targets(text: str) -> Tuple[List[MonitorTarget], List[str]]:
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
            for part in port_parts:
                part = part.strip()
                if '-' in part:
                    try:
                        start_p_str, end_p_str = part.split('-')
                        start_p = int(start_p_str.strip())
                        end_p = int(end_p_str.strip())
                        if start_p > end_p:
                            start_p, end_p = end_p, start_p
                        ports.extend(range(start_p, end_p + 1))
                    except ValueError:
                        errors.append(f"Linha {line_num}: Faixa de portas inválida '{part}'")
                else:
                    try:
                        ports.append(int(part))
                    except ValueError:
                        errors.append(f"Linha {line_num}: Porta inválida '{part}'")

            # Create targets in DB
            for port in ports:
                if not (1 <= port <= 65535):
                    errors.append(f"Linha {line_num}: Porta fora do limite 1-65535: '{port}'")
                    continue

                try:
                    target, created = MonitorTarget.objects.update_or_create(
                        host=host,
                        port=port,
                        defaults={'label': label, 'is_active': True}
                    )
                    created_targets.append(target)
                except Exception as e:
                    logger.error("Erro ao salvar alvo %s:%d: %s", host, port, str(e))
                    errors.append(f"Linha {line_num}: Erro ao salvar alvo no banco de dados.")

        return created_targets, errors


class PortCheckerService:
    """Service to test TCP connections and save performance/uptime logs."""

    @staticmethod
    def check_target(target_id: int) -> str:
        try:
            target = MonitorTarget.objects.get(id=target_id)
        except MonitorTarget.DoesNotExist:
            logger.error("Target ID %d nao encontrado.", target_id)
            return f"Target {target_id} not found."

        if not target.is_active:
            return f"Target {target.host}:{target.port} esta inativo."

        host = target.host
        port = target.port
        timeout = 3.0

        start_time = time.perf_counter()
        status = False

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((host, port))
                status = True
        except (socket.timeout, socket.error) as e:
            logger.info("Conexao falhou para %s:%d: %s", host, port, str(e))
            status = False
        except Exception as e:
            logger.error("Erro inesperado na conexao para %s:%d: %s", host, port, str(e))
            status = False

        end_time = time.perf_counter()
        latency_ms = round((end_time - start_time) * 1000, 2)

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
                target.save(update_fields=['last_checked', 'last_status', 'last_latency'])
        except Exception as e:
            logger.error("Erro ao salvar log de varredura para %s:%d: %s", host, port, str(e))

        status_str = "ABERTA" if status else "FECHADA"
        return f"Checked {host}:{port} -> {status_str} ({latency_ms}ms)"
