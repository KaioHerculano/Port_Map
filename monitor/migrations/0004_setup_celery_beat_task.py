from django.db import migrations

def create_periodic_task(apps, schema_editor):
    IntervalSchedule = apps.get_model('django_celery_beat', 'IntervalSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    
    # Create/get interval of 1 minute
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=1,
        period='minutes'
    )
    
    # Create/get PeriodicTask
    PeriodicTask.objects.get_or_create(
        name='Dispatch Scheduled Checks',
        task='monitor.tasks.dispatch_scheduled_checks',
        defaults={'interval': schedule}
    )

def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(name='Dispatch Scheduled Checks').delete()

class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0003_monitortarget_check_interval'),
        ('django_celery_beat', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, remove_periodic_task),
    ]
