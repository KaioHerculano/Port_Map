from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_repr = models.CharField(max_length=255)
    changes = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        app_label = "monitor"

    def __str__(self):
        user_str = self.user.username if self.user else "Sistema"
        return f"{user_str} - {self.action} {self.model_name} em {self.timestamp.strftime('%d/%m/%Y %H:%M:%S')}"
