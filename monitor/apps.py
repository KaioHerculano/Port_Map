from django.apps import AppConfig


class MonitorConfig(AppConfig):
    name = 'monitor'

    def ready(self) -> None:
        import sys
        # Skip periodic tasks registration during migrations or tests to prevent DB errors
        if 'migrate' in sys.argv or 'makemigrations' in sys.argv or 'test' in sys.argv:
            return
            
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
