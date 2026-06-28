from django.test import TestCase
from django.db import IntegrityError
from django.utils import timezone
from datetime import timedelta

from ..models import MonitorTarget, MonitorLog, Group

class MonitorModelTests(TestCase):
    def test_create_target(self):
        target = MonitorTarget.objects.create(
            label="Servidor de Teste",
            host="127.0.0.1",
            port=8000
        )
        self.assertEqual(str(target), "127.0.0.1:8000 (Servidor de Teste)")
        self.assertTrue(target.is_active)

    def test_duplicate_host_port_raises_integrity_error(self):
        MonitorTarget.objects.create(host="192.168.1.1", port=80)
        with self.assertRaises(IntegrityError):
            MonitorTarget.objects.create(host="192.168.1.1", port=80)

    def test_uptime_percentage_and_average_latency_calculation(self):
        target = MonitorTarget.objects.create(host="127.0.0.1", port=9000, last_status=True)
        
        # Create 3 logs: 2 open, 1 closed
        MonitorLog.objects.create(target=target, status=True, latency=10.5)
        MonitorLog.objects.create(target=target, status=True, latency=15.5)
        MonitorLog.objects.create(target=target, status=False, latency=3.2)
        
        self.assertEqual(target.uptime_percentage_24h, 66.7)
        self.assertEqual(target.average_latency_24h, 13.0)

    def test_uptime_percentage_30d_calculation(self):
        target = MonitorTarget.objects.create(host="127.0.0.1", port=9999, last_status=True)
        now = timezone.now()
        
        # 3 logs inside the 30d window: 2 True, 1 False
        MonitorLog.objects.create(target=target, status=True, latency=10.0)
        MonitorLog.objects.create(target=target, status=True, latency=15.0)
        MonitorLog.objects.create(target=target, status=False, latency=5.0)
        
        # Update timestamps to be spread out in the last 30 days
        logs = list(MonitorLog.objects.filter(target=target))
        MonitorLog.objects.filter(id=logs[0].id).update(timestamp=now - timedelta(days=5))
        MonitorLog.objects.filter(id=logs[1].id).update(timestamp=now - timedelta(days=10))
        MonitorLog.objects.filter(id=logs[2].id).update(timestamp=now - timedelta(days=20))
        
        # 1 log outside the 30d window: True (should be ignored)
        log_outside = MonitorLog.objects.create(target=target, status=True, latency=12.0)
        MonitorLog.objects.filter(id=log_outside.id).update(timestamp=now - timedelta(days=35))
        
        # Availability should be (2/3) * 100 = 66.7
        self.assertEqual(target.uptime_percentage_30d, 66.7)
