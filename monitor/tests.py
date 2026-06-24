from django.test import TestCase
from django.db import IntegrityError
from django.utils import timezone
from datetime import timedelta

from .models import MonitorTarget, MonitorLog
from .services import PortParserService

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


class BulkImportParserTests(TestCase):
    def test_parse_single_target(self):
        text = "45.174.193.10:40001"
        targets, errors = PortParserService.parse_and_create_targets(text)
        
        self.assertEqual(len(targets), 1)
        self.assertEqual(len(errors), 0)
        self.assertEqual(targets[0].host, "45.174.193.10")
        self.assertEqual(targets[0].port, 40001)
        self.assertIsNone(targets[0].label)

    def test_parse_with_labels_brackets(self):
        text = "45.174.193.10:40002 [Servidor de Cameras]"
        targets, errors = PortParserService.parse_and_create_targets(text)
        
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].label, "Servidor de Cameras")

    def test_parse_with_labels_parentheses(self):
        text = "45.174.193.10:40003 (Servidor de Monitoramento)"
        targets, errors = PortParserService.parse_and_create_targets(text)
        
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].label, "Servidor de Monitoramento")

    def test_parse_port_range(self):
        text = "45.174.193.10:40001-40005 [Faixa Principal]"
        targets, errors = PortParserService.parse_and_create_targets(text)
        
        self.assertEqual(len(targets), 5)
        self.assertEqual(len(errors), 0)
        
        ports = [t.port for t in targets]
        self.assertEqual(ports, [40001, 40002, 40003, 40004, 40005])
        for t in targets:
            self.assertEqual(t.host, "45.174.193.10")
            self.assertEqual(t.label, "Faixa Principal")

    def test_parse_multiple_ports_comma_separated(self):
        text = "192.168.0.1:80,443,8080 (Servidor Web)"
        targets, errors = PortParserService.parse_and_create_targets(text)
        
        self.assertEqual(len(targets), 3)
        self.assertEqual(len(errors), 0)
        
        ports = [t.port for t in targets]
        self.assertEqual(ports, [80, 443, 8080])
        for t in targets:
            self.assertEqual(t.host, "192.168.0.1")
            self.assertEqual(t.label, "Servidor Web")

    def test_parse_invalid_lines_accumulates_errors(self):
        text = """
        127.0.0.1:80
        invalidline
        127.0.0.1:90000
        127.0.0.1:abc
        """
        targets, errors = PortParserService.parse_and_create_targets(text)
        
        self.assertEqual(len(targets), 1)
        self.assertEqual(len(errors), 3) # no ports, out of limits, and non-integer port
        self.assertTrue(any("Formato inválido" in err for err in errors))
        self.assertTrue(any("Porta fora do limite" in err for err in errors))
        self.assertTrue(any("Porta inválida" in err for err in errors))
