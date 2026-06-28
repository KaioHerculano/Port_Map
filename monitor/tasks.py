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
        cutoff = now - timedelta(minutes=interval)
        query |= Q(check_interval=interval, last_checked__lte=cutoff)
        
    targets_to_check = MonitorTarget.objects.filter(is_active=True).filter(query)
    count = targets_to_check.count()
    
    if count > 0:
        logger.info("Despachando verificacoes agendadas para %d alvos.", count)
        for target in targets_to_check:
            check_single_target.delay(target.id)
            
    return f"Dispatched scheduled checks for {count} targets."
