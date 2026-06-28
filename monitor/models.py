from django.db import models
from django.utils import timezone
from datetime import timedelta

class Group(models.Model):
    name = models.CharField(
        max_length=255, 
        unique=True, 
        help_text="Nome do grupo (ex: Câmeras Vigia)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class MonitorTarget(models.Model):
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='targets',
        help_text="Grupo ao qual esta porta pertence"
    )
    label = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        help_text="Identificação do servidor ou porta (ex: Servidor Central)"
    )
    host = models.CharField(
        max_length=255, 
        help_text="Endereço IP ou Hostname (ex: 45.174.193.10 ou google.com)"
    )
    port = models.IntegerField(
        help_text="Porta TCP (ex: 40001)"
    )
    is_active = models.BooleanField(
        default=True, 
        help_text="Habilitar/Desabilitar o monitoramento automático deste alvo"
    )
    check_interval = models.IntegerField(
        default=60,
        choices=[
            (1, 'A cada 1 minuto'),
            (5, 'A cada 5 minutos'),
            (15, 'A cada 15 minutos'),
            (30, 'A cada 30 minutos'),
            (48, 'A cada 48 minutos (30 vezes por dia)'),
            (60, 'A cada 1 hora'),
            (120, 'A cada 2 horas'),
            (360, 'A cada 6 horas'),
            (720, 'A cada 12 horas'),
            (1440, 'A cada 24 horas (1 vez por dia)'),
        ],
        help_text="Frequência de verificação deste sensor (em minutos)"
    )
    last_checked = models.DateTimeField(
        blank=True, 
        null=True
    )
    last_status = models.BooleanField(
        blank=True, 
        null=True, 
        help_text="Último status: True para Aberta (online), False para Fechada (offline)"
    )
    last_latency = models.FloatField(
        blank=True, 
        null=True, 
        help_text="Último tempo de resposta em milissegundos"
    )
    created_at = models.DateTimeField(
        auto_now_add=True
    )
    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        unique_together = ('host', 'port')
        ordering = ['host', 'port']

    def __str__(self):
        label_str = f" ({self.label})" if self.label else ""
        return f"{self.host}:{self.port}{label_str}"

    @property
    def uptime_percentage_24h(self):
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
        avg = logs.aggregate(models.Avg('latency'))['latency__avg']
        return round(avg, 2) if avg is not None else 0.0


class MonitorLog(models.Model):
    target = models.ForeignKey(
        MonitorTarget, 
        on_delete=models.CASCADE, 
        related_name='logs'
    )
    status = models.BooleanField(
        help_text="True se a porta estiver aberta, False caso contrário"
    )
    latency = models.FloatField(
        help_text="Tempo de resposta do socket em milissegundos"
    )
    timestamp = models.DateTimeField(
        auto_now_add=True, 
        db_index=True
    )

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        status_str = "ABERTA" if self.status else "FECHADA"
        return f"{self.target} - {status_str} em {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
