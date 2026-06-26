from django.test import TestCase
from django.db import IntegrityError
from django.utils import timezone
from datetime import timedelta

from .models import MonitorTarget, MonitorLog, Group
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

    def test_parse_range_limit_enforced(self):
        # 101 ports in range (limit is 100)
        text = "127.0.0.1:1000-1100"
        targets, errors = PortParserService.parse_and_create_targets(text)
        self.assertEqual(len(targets), 0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(any("Faixa de portas muito grande" in err for err in errors))

    def test_parse_invalid_range_boundaries_rejected(self):
        # Boundary 400063 is out of range, even though the subset of ports is valid, it should be rejected.
        text = "127.0.0.1:40001-400063"
        targets, errors = PortParserService.parse_and_create_targets(text)
        self.assertEqual(len(targets), 0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(any("Faixa de portas fora do limite" in err for err in errors))

    def test_parse_total_limit_enforced(self):
        # Create a text with 6 ranges of 90 ports = 540 ports (limit is 500)
        text = "\n".join([f"127.0.0.{i}:1000-1089" for i in range(1, 7)])
        targets, errors = PortParserService.parse_and_create_targets(text)
        # Should stop after the 5th line (5 * 90 = 450 targets created) and reject the 6th line (450 + 90 = 540 > 500)
        self.assertEqual(len(targets), 450)
        self.assertEqual(len(errors), 1)
        self.assertTrue(any("Limite máximo de 500 alvos" in err for err in errors))

    def test_group_association_and_label_behavior(self):
        # 1. Test parsing of single port with label creates a group and assigns it to both target.group and target.label
        text_single = "192.168.1.1:80 [Servidor Web]"
        targets, errors = PortParserService.parse_and_create_targets(text_single)
        self.assertEqual(len(targets), 1)
        self.assertEqual(len(errors), 0)
        self.assertIsNotNone(targets[0].group)
        self.assertEqual(targets[0].group.name, "Servidor Web")
        self.assertEqual(targets[0].label, "Servidor Web")

        # 2. Test parsing of range port with label creates a group and links targets, but target labels are set to None
        text_range = "45.174.193.10:40001-40003 [Cameras Vigia]"
        targets_range, errors_range = PortParserService.parse_and_create_targets(text_range)
        self.assertEqual(len(targets_range), 3)
        self.assertEqual(len(errors_range), 0)
        
        group = Group.objects.get(name="Cameras Vigia")
        for target in targets_range:
            self.assertEqual(target.group, group)
            self.assertEqual(target.label, "Cameras Vigia")

    def test_explicit_group_association(self):
        group = Group.objects.create(name="Grupo Explicito")
        text = "192.168.10.10:80\n192.168.10.10:443"
        targets, errors = PortParserService.parse_and_create_targets(text, group=group)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].group, group)
        self.assertEqual(targets[1].group, group)

    def test_group_stats_annotation_in_database(self):
        # Create a group
        group = Group.objects.create(name="Servidores BD")
        
        # Create 3 targets under this group
        target1 = MonitorTarget.objects.create(host="10.0.0.1", port=5432, group=group, last_status=True, is_active=True)
        target2 = MonitorTarget.objects.create(host="10.0.0.2", port=5432, group=group, last_status=False, is_active=True)
        target3 = MonitorTarget.objects.create(host="10.0.0.3", port=5432, group=group, is_active=False) # Inactive
        
        # Fetch groups annotated with counts
        from django.db.models import Count, Q
        annotated_group = Group.objects.annotate(
            total_count=Count('targets'),
            online_count=Count('targets', filter=Q(targets__last_status=True, targets__is_active=True)),
            offline_count=Count('targets', filter=Q(targets__last_status=False, targets__is_active=True)),
            inactive_count=Count('targets', filter=Q(targets__is_active=False))
        ).get(id=group.id)
        
        self.assertEqual(annotated_group.total_count, 3)
        self.assertEqual(annotated_group.online_count, 1)
        self.assertEqual(annotated_group.offline_count, 1)
        self.assertEqual(annotated_group.inactive_count, 1)
