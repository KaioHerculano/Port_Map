from django.apps import AppConfig

def setup_periodic_tasks(sender, **kwargs) -> None:
    """Creates the monthly report periodic task after migrations complete."""
    try:
        from django_celery_beat.models import PeriodicTask, CrontabSchedule
        
        # Cron: Run at 08:00 AM on the 1st day of every month
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='8',
            day_of_month='1',
            month_of_year='*',
            day_of_week='*'
        )
        
        PeriodicTask.objects.get_or_create(
            name='Relatório Mensal de SLA - Telegram',
            defaults={
                'crontab': schedule,
                'task': 'monitor.tasks.send_monthly_telegram_report',
                'args': '[]',
                'kwargs': '{}'
            }
        )
    except Exception:
        pass


class MonitorConfig(AppConfig):
    name = 'monitor'

    def ready(self) -> None:
        from django.db.models.signals import post_migrate
        # Connect the setup function to post_migrate to avoid database queries during app initialization
        post_migrate.connect(setup_periodic_tasks, sender=self)
