from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from faker import Faker

from monitor.models import MonitorLog, MonitorTarget

fake = Faker()


class MonitorModelTests(TestCase):
    def test_create_target(self):
        label_val = fake.word()
        host_ip = fake.ipv4()
        target = MonitorTarget.objects.create(label=label_val, host=host_ip, port=8000)
        self.assertEqual(str(target), f"{host_ip}:8000 ({label_val})")
        self.assertTrue(target.is_active)

    def test_allow_duplicate_host_port_for_different_sensors(self):
        host_ip = fake.ipv4()
        t1 = MonitorTarget.objects.create(host=host_ip, port=80, sensor_type="tcp")
        t2 = MonitorTarget.objects.create(host=host_ip, port=80, sensor_type="ping")
        self.assertEqual(t1.host, t2.host)
        self.assertEqual(t1.port, t2.port)

    def test_uptime_percentage_and_average_latency_calculation(self):
        target = MonitorTarget.objects.create(
            host=fake.ipv4(), port=9000, last_status=True
        )

        MonitorLog.objects.create(target=target, status=True, latency=10.5)
        MonitorLog.objects.create(target=target, status=True, latency=15.5)
        MonitorLog.objects.create(target=target, status=False, latency=3.2)

        self.assertEqual(target.uptime_percentage_24h, 66.7)
        self.assertEqual(target.average_latency_24h, 13.0)

    def test_uptime_percentage_30d_calculation(self):
        target = MonitorTarget.objects.create(
            host=fake.ipv4(), port=9999, last_status=True
        )
        now = timezone.now()

        MonitorLog.objects.create(target=target, status=True, latency=10.0)
        MonitorLog.objects.create(target=target, status=True, latency=15.0)
        MonitorLog.objects.create(target=target, status=False, latency=5.0)

        logs = list(MonitorLog.objects.filter(target=target))
        MonitorLog.objects.filter(id=logs[0].id).update(
            timestamp=now - timedelta(days=5)
        )
        MonitorLog.objects.filter(id=logs[1].id).update(
            timestamp=now - timedelta(days=10)
        )
        MonitorLog.objects.filter(id=logs[2].id).update(
            timestamp=now - timedelta(days=20)
        )

        log_outside = MonitorLog.objects.create(
            target=target, status=True, latency=12.0
        )
        MonitorLog.objects.filter(id=log_outside.id).update(
            timestamp=now - timedelta(days=35)
        )

        self.assertEqual(target.uptime_percentage_30d, 66.7)

    def test_device_model_creation_and_sensors(self):
        from monitor.models import Device

        device_name = fake.company()
        host_ip = fake.ipv4()
        device = Device.objects.create(
            name=device_name, host=host_ip, device_type="parks_olt"
        )
        self.assertEqual(str(device), f"{device_name} ({host_ip})")

        sensor = MonitorTarget.objects.create(
            device=device,
            host=device.host,
            sensor_type="ping",
            label=f"{device_name} - Ping",
        )
        self.assertEqual(device.sensors.count(), 1)
        self.assertEqual(device.sensors.first(), sensor)

    def test_string_representations_and_empty_uptime(self):
        from django.contrib.auth import get_user_model

        from monitor.models import AuditLog, DailySummary, Group

        # Test Group __str__
        group = Group.objects.create(name="Grupo A")
        self.assertEqual(str(group), "Grupo A")

        # Test MonitorTarget uptime when no logs exist
        target_status_true = MonitorTarget.objects.create(
            host="192.168.1.1", port=80, last_status=True
        )
        target_status_false = MonitorTarget.objects.create(
            host="192.168.1.2", port=80, last_status=False
        )
        self.assertEqual(target_status_true.uptime_percentage_24h, 100.0)
        self.assertEqual(target_status_true.uptime_percentage_30d, 100.0)
        self.assertEqual(target_status_false.uptime_percentage_24h, 0.0)
        self.assertEqual(target_status_false.uptime_percentage_30d, 0.0)

        # Test MonitorLog __str__
        log_open = MonitorLog.objects.create(
            target=target_status_true, status=True, latency=10.0
        )
        log_closed = MonitorLog.objects.create(
            target=target_status_true, status=False, latency=0.0
        )
        self.assertIn("ABERTA", str(log_open))
        self.assertIn("FECHADA", str(log_closed))

        # Test DailySummary __str__
        summary = DailySummary.objects.create(
            target=target_status_true,
            date=timezone.localdate(),
            availability=99.5,
            avg_latency=12.4,
        )
        self.assertIn("99.5%", str(summary))

        # Test AuditLog __str__ (with and without user)
        audit_system = AuditLog.objects.create(
            action="Criar",
            model_name="Dispositivo",
            object_repr="Rep",
            changes="Criou",
        )
        self.assertIn("Sistema", str(audit_system))

        User = get_user_model()
        user = User.objects.create_user(username="testaudituser", password="password")
        audit_user = AuditLog.objects.create(
            user=user,
            action="Criar",
            model_name="Dispositivo",
            object_repr="Rep",
            changes="Criou",
        )
        self.assertIn("testaudituser", str(audit_user))
