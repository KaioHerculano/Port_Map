from django.test import TestCase
from django.db import IntegrityError

from ..models import MonitorTarget, MonitorLog, Group
from ..services import PortParserService

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

        # 2. Test parsing of range port with label creates a group and links targets
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

    def test_group_association_respects_explicit_selection_with_label(self):
        group = Group.objects.create(name="Grupo Vigia")
        text = "192.168.1.1:80 [Camera Frontal]"
        targets, errors = PortParserService.parse_and_create_targets(text, group=group)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].group, group)
        self.assertEqual(targets[0].label, "Camera Frontal")
        # Ensure we didn't create a group named "Camera Frontal"
        self.assertFalse(Group.objects.filter(name="Camera Frontal").exists())


from unittest.mock import patch, MagicMock, ANY
from ..services import PortCheckerService

class PortCheckerServiceTests(TestCase):
    @patch('monitor.utils.send_telegram_alert')
    @patch('socket.socket')
    def test_telegram_alert_on_status_change(self, mock_socket, mock_send_telegram_alert):
        # Setup socket mock to connect successfully (open port)
        mock_socket_inst = MagicMock()
        mock_socket.return_value.__enter__.return_value = mock_socket_inst
        
        target = MonitorTarget.objects.create(host="127.0.0.1", port=80, last_status=False, telegram_alert_threshold=1)
        # Create preceding failure log
        MonitorLog.objects.create(target=target, status=False, latency=10.0)
        
        # Test transition: False -> True
        PortCheckerService.check_target(target.id)
        
        # Assert recovery alert was triggered
        mock_send_telegram_alert.assert_called_once_with(target, False, True, downtime_duration=ANY)

    @patch('monitor.utils.send_telegram_alert')
    @patch('socket.socket')
    def test_telegram_no_alert_when_status_unchanged(self, mock_socket, mock_send_telegram_alert):
        mock_socket_inst = MagicMock()
        mock_socket.return_value.__enter__.return_value = mock_socket_inst
        
        target = MonitorTarget.objects.create(host="127.0.0.1", port=80, last_status=True, telegram_alert_threshold=1)
        MonitorLog.objects.create(target=target, status=True, latency=10.0)
        
        # Test transition: True -> True (no change)
        PortCheckerService.check_target(target.id)
        
        # Assert alert was NOT triggered
        mock_send_telegram_alert.assert_not_called()

    @patch('monitor.utils.send_telegram_alert')
    @patch('socket.socket')
    def test_telegram_alert_on_first_failure(self, mock_socket, mock_send_telegram_alert):
        # Setup socket mock to fail connection
        mock_socket.return_value.__enter__.return_value.connect.side_effect = Exception("Connection refused")
        
        target = MonitorTarget.objects.create(host="127.0.0.1", port=80, last_status=None, telegram_alert_threshold=1)
        
        # Test transition: None -> False (first check failed)
        PortCheckerService.check_target(target.id)
        
        # Assert alert was triggered
        mock_send_telegram_alert.assert_called_once_with(target, None, False, downtime_duration=None)

    @patch('monitor.utils.send_telegram_alert')
    @patch('socket.socket')
    def test_telegram_alert_on_consecutive_failures_threshold(self, mock_socket, mock_send_telegram_alert):
        # Setup socket mock to fail connection
        mock_socket.return_value.__enter__.return_value.connect.side_effect = Exception("Connection refused")
        
        target = MonitorTarget.objects.create(host="127.0.0.1", port=80, last_status=False, telegram_alert_threshold=2)
        
        # 1st failure check: consecutive count becomes 1 (threshold is 2), should NOT alert
        PortCheckerService.check_target(target.id)
        mock_send_telegram_alert.assert_not_called()
        
        # 2nd failure check: consecutive count becomes 2 (threshold is 2), should ALERT!
        PortCheckerService.check_target(target.id)
        mock_send_telegram_alert.assert_called_once_with(target, False, False, downtime_duration=None)
