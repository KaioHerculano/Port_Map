from datetime import timedelta

from django.db import models
from django.utils import timezone

from .choices import CHECK_INTERVAL_CHOICES, SENSOR_TYPES, TELEGRAM_ALERT_CHOICES
from .device import Device
from .group import Group
from .validators import validate_host


class MonitorTarget(models.Model):
    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="targets",
    )
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, null=True, blank=True, related_name="sensors"
    )
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True, related_name="targets"
    )
    label = models.CharField(max_length=255, blank=True, null=True)
    host = models.CharField(max_length=255, validators=[validate_host])
    port = models.IntegerField(null=True, blank=True)
    sensor_type = models.CharField(max_length=50, default="tcp", choices=SENSOR_TYPES)
    sensor_identifier = models.CharField(max_length=255, default="", blank=True)
    sensor_value = models.CharField(max_length=255, blank=True, null=True)
    last_counter_val = models.BigIntegerField(blank=True, null=True)
    last_counter_time = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    check_interval = models.IntegerField(default=60, choices=CHECK_INTERVAL_CHOICES)
    last_checked = models.DateTimeField(blank=True, null=True)
    last_status = models.BooleanField(blank=True, null=True)
    last_latency = models.FloatField(blank=True, null=True)
    telegram_alert_threshold = models.IntegerField(
        choices=TELEGRAM_ALERT_CHOICES, default=1
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["host", "port", "id"]
        app_label = "monitor"

    def __str__(self):
        label_str = f" ({self.label})" if self.label else ""
        if self.sensor_type == "tcp":
            return f"{self.host}:{self.port}{label_str}"
        else:
            sensor_name = self.sensor_identifier or self.get_sensor_type_display()
            return f"{self.host} - {sensor_name}{label_str}"

    @property
    def uptime_percentage_24h(self):
        # TODO: Refatorar futuramente para nao usar count dinamico, por hora otimizado removendo comments
        now = timezone.now()
        yesterday = now - timedelta(hours=24)
        logs = self.logs.filter(timestamp__gte=yesterday)
        total = logs.count()
        if total == 0:
            return 100.0 if self.last_status else 0.0
        success = logs.filter(status=True).count()
        return round((success / total) * 100, 1)

    @property
    def uptime_percentage_30d(self):
        now = timezone.now()
        start_date = now - timedelta(days=30)
        logs = self.logs.filter(timestamp__gte=start_date)
        total = logs.count()
        if total == 0:
            return 100.0 if self.last_status else 0.0
        success = logs.filter(status=True).count()
        return round((success / total) * 100, 1)

    @property
    def average_latency_24h(self):
        now = timezone.now()
        yesterday = now - timedelta(hours=24)
        logs = self.logs.filter(timestamp__gte=yesterday, status=True)
        avg = logs.aggregate(models.Avg("latency"))["latency__avg"]
        return round(avg, 2) if avg is not None else 0.0
