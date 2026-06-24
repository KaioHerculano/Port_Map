import logging
from celery import shared_task
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
