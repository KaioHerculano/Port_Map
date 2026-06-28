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

        old_status = target.last_status
        preceding_failures = 0
        downtime_duration_str = None
        if status and old_status == False:
            # Count consecutive failures before writing the new log and locate downtime start
            last_logs = target.logs.all().order_by('-timestamp')[:100]
            downtime_start = None
            for log in last_logs:
                if not log.status:
                    preceding_failures += 1
                    downtime_start = log.timestamp
                else:
                    break
            
            if downtime_start:
                now = timezone.now()
                duration = now - downtime_start
                total_seconds = int(duration.total_seconds())
                if total_seconds < 60:
                    downtime_duration_str = f"{total_seconds}s"
                elif total_seconds < 3600:
                    minutes = total_seconds // 60
                    seconds = total_seconds % 60
                    downtime_duration_str = f"{minutes}m {seconds}s"
                else:
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    downtime_duration_str = f"{hours}h {minutes}m"

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
            return f"Error saving log: {str(e)}"

        # Trigger Telegram alert outside the transaction to prevent database locking
        if target.telegram_alert_threshold > 0:
            should_alert = False
            
            if not status:
                # Count consecutive failures including the one we just wrote
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
                # Recovery alert: previous was False and preceding failure count reached threshold
                if old_status == False and preceding_failures >= target.telegram_alert_threshold:
                    should_alert = True
                    
            if should_alert:
                try:
                    from .utils import send_telegram_alert
                    send_telegram_alert(target, old_status, status, downtime_duration=downtime_duration_str)
                except Exception as tel_err:
                    logger.error("Falha ao invocar alerta do Telegram: %s", str(tel_err))

        status_str = "ABERTA" if status else "FECHADA"
        return f"Checked {host}:{port} -> {status_str} ({latency_ms}ms)"
