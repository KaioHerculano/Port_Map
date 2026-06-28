import urllib.request
import urllib.parse
import json
import logging
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

def send_telegram_message(text: str) -> bool:
    """Send a raw text message to the configured Telegram Chat using the Bot API."""
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
    
    if not token or not chat_id:
        # Silently bypass if not configured
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get('ok'):
                logger.info("Notificacao do Telegram enviada com sucesso.")
                return True
            else:
                logger.error("Erro na resposta do Telegram: %s", res_data)
                return False
    except Exception as e:
        logger.error("Falha ao enviar mensagem para o Telegram: %s", str(e))
        return False


def send_telegram_alert(target, old_status, new_status) -> bool:
    """Format and send a structured HTML status alert to the Telegram Chat."""
    label = target.label or "Sem identificação"
    group_name = target.group.name if target.group else "Sem grupo"
    
    # Get current time in local timezone (Cuiaba)
    local_time = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S')
    
    if new_status:
        emoji = "🟢"
        title = "Dispositivo Restabelecido!"
    else:
        emoji = "🔴"
        title = "Dispositivo Offline!"
        
    message = (
        f"{emoji} <b>{title}</b>\n\n"
        f"<b>Identificação:</b> {label}\n"
        f"<b>IP:</b> <code>{target.host}</code>\n"
        f"<b>Porta:</b> <code>{target.port}</code>\n"
        f"<b>Grupo:</b> {group_name}\n"
        f"<b>Horário:</b> {local_time}"
    )
    
    return send_telegram_message(message)
