from django.db import models

from .target import MonitorTarget


class MonitorLog(models.Model):
    target = models.ForeignKey(
        MonitorTarget, on_delete=models.CASCADE, related_name="logs"
    )
    status = models.BooleanField()
    latency = models.FloatField()
    metric_value = models.FloatField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        app_label = "monitor"

    def __str__(self):
        status_str = "ABERTA" if self.status else "FECHADA"
        return f"{self.target} - {status_str} em {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"


class DailySummary(models.Model):
    target = models.ForeignKey(
        MonitorTarget, on_delete=models.CASCADE, related_name="daily_summaries"
    )
    date = models.DateField(db_index=True)
    availability = models.FloatField()
    avg_latency = models.FloatField()

    class Meta:
        unique_together = ("target", "date")
        ordering = ["-date", "target"]
        app_label = "monitor"

    def __str__(self):
        return f"{self.target} - {self.date}: {self.availability}%"
