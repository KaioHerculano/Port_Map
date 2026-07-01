import os

from celery import Celery

# Set default Django settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

app = Celery("app")

# Load task configuration from settings.py using CELERY_ prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks in tasks.py of installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
