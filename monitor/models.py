import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def validate_host(value):
    hostname_regex = re.compile(
        r"^([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])"
        r"(\.([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]))*$"
    )
    ip_regex = re.compile(
        r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    )
    if not hostname_regex.match(value) and not ip_regex.match(value):
        raise ValidationError("O host/IP deve ser um IP válido ou nome de domínio.")


CHECK_INTERVAL_CHOICES = [
    (1, "A cada 1 minuto"),
    (5, "A cada 5 minutos"),
    (15, "A cada 15 minutos"),
    (30, "A cada 30 minutos"),
    (48, "A cada 48 minutos (30 vezes por dia)"),
    (60, "A cada 1 hora"),
    (120, "A cada 2 horas"),
    (360, "A cada 6 horas"),
    (720, "A cada 12 horas"),
    (1440, "A cada 24 horas (1 vez por dia)"),
]

TELEGRAM_ALERT_CHOICES = [
    (1, "Imediatamente (1ª falha)"),
    (2, "Após 2 falhas consecutivas"),
    (3, "Após 3 falhas consecutivas"),
    (0, "Desativar Alertas (Não notificar)"),
]


class Group(models.Model):
    name = models.CharField(
        max_length=255, unique=True, help_text="Nome do grupo (ex: Câmeras Vigia)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Device(models.Model):
    DEVICE_TYPES = [
        ("mikrotik_snmp", "MikroTik (SNMP)"),
        ("parks_olt", "OLT Parks GPON"),
        ("generic_snmp", "Genérico (SNMP)"),
        ("generic_ping", "Genérico (Apenas Ping)"),
    ]

    name = models.CharField(
        max_length=255,
        help_text="Nome amigável do equipamento (ex: OLT Parks, MikroTik BGP)",
    )
    host = models.CharField(
        max_length=255,
        validators=[validate_host],
        help_text="Endereço IP ou Hostname (ex: 172.31.255.2)",
    )
    device_type = models.CharField(
        max_length=50,
        choices=DEVICE_TYPES,
        default="mikrotik_snmp",
        help_text="Tipo de equipamento para comunicação e coleta de dados",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="devices",
        help_text="Grupo ao qual este equipamento pertence",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Habilitar/Desabilitar monitoramento de todos os sensores deste dispositivo",
    )

    # SNMP configurations
    snmp_community = models.CharField(
        max_length=255, default="public", help_text="Comunidade SNMP v2c (ex: public)"
    )
    snmp_port = models.IntegerField(default=161, help_text="Porta SNMP (padrão: 161)")

    check_interval = models.IntegerField(
        default=60,
        choices=CHECK_INTERVAL_CHOICES,
        help_text="Frequência de verificação padrão para os sensores deste equipamento (em minutos)",
    )
    telegram_alert_threshold = models.IntegerField(
        default=1,
        choices=TELEGRAM_ALERT_CHOICES,
        help_text="Regra de alerta de Telegram padrão para os sensores deste equipamento",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.host})"


class MonitorTarget(models.Model):
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sensors",
        help_text="Dispositivo ao qual este sensor pertence",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targets",
        help_text="Grupo ao qual esta porta pertence",
    )
    label = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Identificação do servidor ou porta (ex: Servidor Central)",
    )
    host = models.CharField(
        max_length=255,
        validators=[validate_host],
        help_text="Endereço IP ou Hostname (ex: 10.0.0.1 ou google.com)",
    )
    port = models.IntegerField(
        null=True,
        blank=True,
        help_text="Porta TCP (ex: 8080) - Opcional para sensores não-TCP",
    )
    sensor_type = models.CharField(
        max_length=50,
        default="tcp",
        choices=[
            ("tcp", "Porta TCP"),
            ("ping", "Ping (ICMP)"),
            ("snmp_traffic", "Tráfego SNMP"),
            ("snmp_numeric", "Valor Numérico SNMP"),
        ],
        help_text="Tipo de monitoramento/coleta",
    )
    sensor_identifier = models.CharField(
        max_length=255,
        default="",
        blank=True,
        help_text="Identificador único do sensor (ex: OID ou nome da interface)",
    )
    sensor_value = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Valor formatado da última coleta",
    )
    last_counter_val = models.BigIntegerField(
        blank=True, null=True, help_text="Último valor bruto de bytes (para tráfego)"
    )
    last_counter_time = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Timestamp do último valor bruto (para tráfego)",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Habilitar/Desabilitar o monitoramento automático deste alvo",
    )
    check_interval = models.IntegerField(
        default=60,
        choices=CHECK_INTERVAL_CHOICES,
        help_text="Frequência de verificação deste sensor (em minutos)",
    )
    last_checked = models.DateTimeField(blank=True, null=True)
    last_status = models.BooleanField(
        blank=True,
        null=True,
        help_text="Último status: True para Aberta (online), False para Fechada (offline)",
    )
    last_latency = models.FloatField(
        blank=True, null=True, help_text="Último tempo de resposta em milissegundos"
    )
    telegram_alert_threshold = models.IntegerField(
        choices=TELEGRAM_ALERT_CHOICES,
        default=1,
        help_text="Regra para disparo de alertas de falha no Telegram",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["host", "port", "id"]

    def __str__(self):
        label_str = f" ({self.label})" if self.label else ""
        if self.sensor_type == "tcp":
            return f"{self.host}:{self.port}{label_str}"
        else:
            sensor_name = self.sensor_identifier or self.get_sensor_type_display()
            return f"{self.host} - {sensor_name}{label_str}"

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
        avg = logs.aggregate(models.Avg("latency"))["latency__avg"]
        return round(avg, 2) if avg is not None else 0.0


class MonitorLog(models.Model):
    target = models.ForeignKey(
        MonitorTarget, on_delete=models.CASCADE, related_name="logs"
    )
    status = models.BooleanField(
        help_text="True se a porta estiver aberta, False caso contrário"
    )
    latency = models.FloatField(
        help_text="Tempo de resposta do socket em milissegundos"
    )
    metric_value = models.FloatField(
        null=True,
        blank=True,
        help_text="Valor numérico bruto da métrica coletada (ex: graus, %, Mbps)",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        status_str = "ABERTA" if self.status else "FECHADA"
        return f"{self.target} - {status_str} em {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"


class DailySummary(models.Model):
    target = models.ForeignKey(
        MonitorTarget, on_delete=models.CASCADE, related_name="daily_summaries"
    )
    date = models.DateField(db_index=True)
    availability = models.FloatField(
        help_text="Porcentagem de disponibilidade diária (0 a 100)"
    )
    avg_latency = models.FloatField(help_text="Latência média em milissegundos")

    class Meta:
        unique_together = ("target", "date")
        ordering = ["-date", "target"]

    def __str__(self):
        return f"{self.target} - {self.date}: {self.availability}%"


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(
        max_length=50,
        help_text="Ação executada (ex: Criar, Editar, Excluir, Ativar, Desativar, Lote)",
    )
    model_name = models.CharField(
        max_length=100, help_text="Modelo modificado (ex: Dispositivo, Grupo)"
    )
    object_repr = models.CharField(
        max_length=255,
        help_text="Representação do objeto (ex: Câmera Portão - 10.0.0.1:8080)",
    )
    changes = models.TextField(
        blank=True, null=True, help_text="Descrição amigável das alterações efetuadas"
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        user_str = self.user.username if self.user else "Sistema"
        return f"{user_str} - {self.action} {self.model_name} em {self.timestamp.strftime('%d/%m/%Y %H:%M:%S')}"
