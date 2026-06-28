import logging
from celery import shared_task
from django.utils import timezone
from .models import MonitorTarget
from .services import PortCheckerService

logger = logging.getLogger(__name__)


@shared_task
def check_single_target(target_id: int) -> str:
    """Celery task to run TCP check on a target using PortCheckerService."""
    logger.info("Iniciando varredura para Target ID: %d", target_id)
    result = PortCheckerService.check_target(target_id)
    logger.info("Resultado da varredura para Target ID %d: %s", target_id, result)
    return result


@shared_task
def check_all_targets() -> str:
    """Celery task to trigger TCP checks for all active targets in parallel."""
    active_targets = MonitorTarget.objects.filter(is_active=True)
    count = active_targets.count()
    logger.info("Despachando verificacoes paralela para %d alvos ativos.", count)

    for target in active_targets:
        check_single_target.delay(target.id)

    return f"Dispatched check tasks for {count} targets."


@shared_task
def dispatch_scheduled_checks() -> str:
    """
    Celery task to run periodically (e.g. every 1 minute) to check which targets
    need to be scanned based on their check_interval and last_checked.
    """
    from django.db.models import Q
    from datetime import timedelta
    
    now = timezone.now()
    query = Q(last_checked__isnull=True)
    
    # Fetch unique check intervals currently used by active targets
    active_intervals = MonitorTarget.objects.filter(is_active=True).values_list('check_interval', flat=True).distinct()
    
    for interval in active_intervals:
        # Add a 10-second buffer to handle scheduling jitter and execution delay
        cutoff = now - timedelta(minutes=interval) + timedelta(seconds=10)
        query |= Q(check_interval=interval, last_checked__lte=cutoff)
        
    targets_to_check = MonitorTarget.objects.filter(is_active=True).filter(query)
    count = targets_to_check.count()
    
    if count > 0:
        logger.info("Despachando verificacoes agendadas para %d alvos.", count)
        for target in targets_to_check:
            check_single_target.delay(target.id)
            
    return f"Dispatched scheduled checks for {count} targets."


@shared_task
def send_monthly_telegram_report() -> str:
    """
    Celery task that runs on the 1st of every month to send a report of the previous month's SLA
    and target availability to Telegram.
    """
    from django.db.models import Count, Q, Case, When, Value, FloatField, F
    from django.db.models.functions import Cast
    from django.utils import timezone
    from datetime import datetime, timedelta
    from django.conf import settings
    from .utils import send_telegram_message
    
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        return "Telegram not configured. Monthly report skipped."
        
    now = timezone.now()
    # Go back to the first day of the current month
    first_day_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Go back one day to get into the previous month
    last_day_prev_month = first_day_current_month - timedelta(seconds=1)
    # Get the first day of the previous month
    first_day_prev_month = last_day_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Calculate previous month date strings
    month_names_pt = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
        5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
        9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }
    month_name = month_names_pt.get(first_day_prev_month.month, "")
    month_year_str = f"{first_day_prev_month.month:02d}/{first_day_prev_month.year}"
    
    # Calculate availability for each active target
    targets = MonitorTarget.objects.filter(is_active=True).annotate(
        total_logs=Count('logs', filter=Q(logs__timestamp__gte=first_day_prev_month, logs__timestamp__lte=last_day_prev_month)),
        success_logs=Count('logs', filter=Q(logs__timestamp__gte=first_day_prev_month, logs__timestamp__lte=last_day_prev_month, logs__status=True))
    ).annotate(
        availability=Case(
            When(total_logs=0, then=Case(
                When(last_status=True, then=Value(100.0)),
                default=Value(0.0)
            )),
            default=Cast(F('success_logs') * 100.0 / F('total_logs'), output_field=FloatField())
        )
    )
    
    total_sensors = targets.count()
    if total_sensors == 0:
        return "No active sensors to report."
        
    excelente_count = 0
    bom_count = 0
    critico_count = 0
    total_avail_sum = 0.0
    low_targets = []
    
    for t in targets:
        avail = round(t.availability, 1)
        total_avail_sum += avail
        
        if avail >= 80.0:
            excelente_count += 1
        elif avail >= 70.0:
            bom_count += 1
        else:
            critico_count += 1
            
        if avail < 50.0:
            low_targets.append(t)
            
    avg_availability = round(total_avail_sum / total_sensors, 1)
    if avg_availability >= 80.0:
        status_geral = "Excelente 🟢"
    elif avg_availability >= 70.0:
        status_geral = "Bom 🟡"
    else:
        status_geral = "Crítico 🔴"
        
    message = (
        f"📊 <b>Relatório Mensal - {month_year_str}</b>\n\n"
        f"Estatísticas gerais de disponibilidade dos dispositivos no mês de {month_name}.\n\n"
        f"<b>Resumo de SLA:</b>\n"
        f"• Geral: <code>{avg_availability}%</code> ({status_geral})\n"
        f"• Sensores Monitorados: {total_sensors}\n\n"
        f"<b>Distribuição por Status:</b>\n"
        f"• Excelente (≥80%): {excelente_count}\n"
        f"• Bom (70-79.9%): {bom_count}\n"
        f"• Crítico (&lt;70%): {critico_count}\n\n"
    )
    
    if low_targets:
        message += "⚠️ <b>Dispositivos Críticos (&lt;50%):</b>\n"
        for t in low_targets:
            label_str = t.label or f"{t.host}:{t.port}"
            message += f"• {label_str} (<code>{t.host}:{t.port}</code>): <b>{t.availability:.1f}%</b>\n"
    else:
        message += "✅ <b>Nenhum dispositivo ficou abaixo de 50% de disponibilidade!</b>"
        
    success = send_telegram_message(message)
    if success:
        return f"Monthly report sent successfully. Avg availability: {avg_availability}%"
    else:
        return "Failed to send monthly report message."
