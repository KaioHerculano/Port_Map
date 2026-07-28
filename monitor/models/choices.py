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

DEVICE_TYPES = [
    ("mikrotik_snmp", "MikroTik (SNMP)"),
    ("parks_olt", "OLT Parks GPON"),
    ("generic_snmp", "Genérico (SNMP)"),
    ("generic_ping", "Genérico (Apenas Ping)"),
]

SENSOR_TYPES = [
    ("tcp", "Porta TCP"),
    ("ping", "Ping (ICMP)"),
    ("snmp_traffic", "Tráfego SNMP"),
    ("snmp_numeric", "Valor Numérico SNMP"),
]
