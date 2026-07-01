import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def send_telegram_message(text: str) -> bool:
    """Send a raw text message to the configured Telegram Chat using the Bot API."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        # Silently bypass if not configured
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:  # nosec B310
            res_data = json.loads(response.read().decode("utf-8"))
            if res_data.get("ok"):
                logger.info("Notificacao do Telegram enviada com sucesso.")
                return True
            else:
                logger.error("Erro na resposta do Telegram: %s", res_data)
                return False
    except Exception as e:
        logger.error("Falha ao enviar mensagem para o Telegram: %s", str(e))
        return False


def send_telegram_alert(
    target, old_status, new_status, downtime_duration: Optional[str] = None
) -> bool:
    """Format and send a structured HTML status alert to the Telegram Chat."""
    label = target.label or "Sem identificação"
    group_name = target.group.name if target.group else "Sem grupo"

    local_time = timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M:%S")

    if new_status:
        title = "Sensor Restabelecido!"
    else:
        title = "Sensor Offline / Falha!"

    if target.sensor_type == "tcp":
        detail_label = "Porta"
        detail_value = str(target.port)
    else:
        detail_label = "Tipo de Sensor"
        detail_value = target.get_sensor_type_display()

    message = (
        f"<b>{title}</b>\n\n"
        f"<b>Identificação:</b> {label}\n"
        f"<b>IP/Host:</b> <code>{target.host}</code>\n"
        f"<b>{detail_label}:</b> <code>{detail_value}</code>\n"
    )

    if target.sensor_value:
        message += f"<b>Valor:</b> <code>{target.sensor_value}</code>\n"

    message += f"<b>Grupo:</b> {group_name}\n"

    if new_status and downtime_duration:
        message += f"<b>Tempo Offline:</b> <code>{downtime_duration}</code>\n"

    message += f"<b>Horário:</b> {local_time}"

    return send_telegram_message(message)
