from django.db import models

from .choices import CHECK_INTERVAL_CHOICES, DEVICE_TYPES, TELEGRAM_ALERT_CHOICES
from .group import Group
from .validators import validate_host


class Device(models.Model):
    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="devices",
    )
    name = models.CharField(max_length=255)
    host = models.CharField(max_length=255, validators=[validate_host])
    device_type = models.CharField(
        max_length=50, choices=DEVICE_TYPES, default="mikrotik_snmp"
    )
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True, related_name="devices"
    )
    is_active = models.BooleanField(default=True)
    snmp_community = models.CharField(max_length=255, default="public")
    snmp_port = models.IntegerField(default=161)
    check_interval = models.IntegerField(default=60, choices=CHECK_INTERVAL_CHOICES)
    telegram_alert_threshold = models.IntegerField(
        default=1, choices=TELEGRAM_ALERT_CHOICES
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        app_label = "monitor"

    def __str__(self):
        return f"{self.name} ({self.host})"
