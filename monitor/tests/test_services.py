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

    @patch('monitor.utils.send_telegram_message')
    def test_send_monthly_telegram_report(self, mock_send_telegram_message):
        mock_send_telegram_message.return_value = True
        
        # Create targets
        target_ok = MonitorTarget.objects.create(host="192.168.1.10", port=80, last_status=True, is_active=True)
        target_fail = MonitorTarget.objects.create(host="192.168.1.20", port=80, last_status=False, is_active=True)
        
        # Determine previous month
        from django.utils import timezone
        from datetime import timedelta
        now = timezone.now()
        first_day_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_time = first_day_current_month - timedelta(days=5)
        
        # target_ok availability: 100%
        log_ok = MonitorLog.objects.create(target=target_ok, status=True, latency=15.0)
        MonitorLog.objects.filter(id=log_ok.id).update(timestamp=prev_month_time)
        
        # target_fail availability: 0%
        log_fail = MonitorLog.objects.create(target=target_fail, status=False, latency=10.0)
        MonitorLog.objects.filter(id=log_fail.id).update(timestamp=prev_month_time)
        
        from ..tasks import send_monthly_telegram_report
        with self.settings(TELEGRAM_BOT_TOKEN='token', TELEGRAM_CHAT_ID='chat_id'):
            res = send_monthly_telegram_report()
            
        self.assertIn("Monthly report sent successfully", res)
        mock_send_telegram_message.assert_called_once()
        
        # Verify message text format contains Critical classification and details of target_fail (SLA < 50%)
        call_args = mock_send_telegram_message.call_args[0][0]
        self.assertIn("Relatório Mensal", call_args)
        self.assertIn("192.168.1.20:80", call_args)  # Critical target listed


from ..models import Device
from ..services import DeviceDiscoveryService

class MikroTikAdvancedMonitoringTests(TestCase):
    def setUp(self):
        self.device_api = Device.objects.create(
            name="MikroTik API Test",
            host="192.168.88.1",
            device_type="mikrotik",
            api_username="admin",
            api_password="password",
            check_interval=5,
            telegram_alert_threshold=2
        )
        self.device_snmp = Device.objects.create(
            name="MikroTik SNMP Test",
            host="192.168.88.2",
            device_type="mikrotik_snmp",
            snmp_community="public",
            snmp_port=161,
            check_interval=5,
            telegram_alert_threshold=2
        )

    @patch('monitor.services.MikrotikAPI')
    def test_discover_and_provision_api_sensors(self, mock_api_class):
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api_class.return_value = mock_api
        
        # Mock talk responses for API
        def mock_talk(sentence):
            cmd = sentence[0]
            if cmd == "/interface/print":
                return [['!re', '=name=ether1'], ['!re', '=name=ether2']]
            elif cmd == "/system/resource/cpu/print":
                return [['!re', '=cpu=0', '=load=12'], ['!re', '=cpu=1', '=load=24']]
            elif cmd == "/system/health/print":
                return [['!re', '=name=voltage', '=value=24.2'], ['!re', '=name=temperature', '=value=45']]
            elif cmd == "/routing/bgp/peer/print":
                return [['!re', '=name=peer1', '=remote-address=10.0.0.1']]
            return []
            
        mock_api.talk.side_effect = mock_talk
        
        sensors, error = DeviceDiscoveryService.discover_interfaces(self.device_api)
        self.assertIsNone(error)
        self.assertEqual(len(sensors), 7) # 2 interfaces, 2 CPUs, 2 health, 1 BGP peer
        
        identifiers = [s['identifier'] for s in sensors]
        self.assertIn("traffic:ether1", identifiers)
        self.assertIn("cpu:0", identifiers)
        self.assertIn("health:voltage", identifiers)
        self.assertIn("bgp:peer1", identifiers)
        
        # Provision them
        count = DeviceDiscoveryService.provision_sensors(self.device_api, ["traffic:ether1", "cpu:0", "health:voltage", "bgp:peer1"])
        self.assertEqual(count, 4)
        
        targets = self.device_api.sensors.all()
        self.assertEqual(targets.count(), 4)
        
        # Check custom check_interval and telegram rule propagation
        cpu_target = targets.get(sensor_identifier="cpu:0")
        self.assertEqual(cpu_target.check_interval, 5)
        self.assertEqual(cpu_target.telegram_alert_threshold, 2)
        
        # Check that temperature health metric has minimum of 5 minutes interval
        volt_target = targets.get(sensor_identifier="health:voltage")
        self.assertEqual(volt_target.check_interval, 5)

    @patch('monitor.services.PortCheckerService.snmp_walk')
    @patch('monitor.services.PortCheckerService._snmp_get')
    def test_discover_and_provision_snmp_sensors(self, mock_snmp_get, mock_snmp_walk):
        # Walk returns list of (oid_str, value)
        def mock_walk(host, community, port, oid):
            if oid == "1.3.6.1.2.1.2.2.1.2":
                return [("1.3.6.1.2.1.2.2.1.2.1", "ether1"), ("1.3.6.1.2.1.2.2.1.2.2", "ether2")]
            elif oid == "1.3.6.1.2.1.25.3.3.1.2":
                return [("1.3.6.1.2.1.25.3.3.1.2.1", "12"), ("1.3.6.1.2.1.25.3.3.1.2.2", "24")]
            elif oid == "1.3.6.1.2.1.15.3.1.2":
                return [("1.3.6.1.2.1.15.3.1.2.10.0.0.1", "6")]
            elif oid == "1.3.6.1.4.1.14988.1.1.3":
                return [
                    ("1.3.6.1.4.1.14988.1.1.3.8.0", "242"), # Voltage
                    ("1.3.6.1.4.1.14988.1.1.3.10.0", "450") # CPU Temperature
                ]
            return []
        mock_snmp_walk.side_effect = mock_walk
        mock_snmp_get.return_value = (False, None)
        
        sensors, error = DeviceDiscoveryService.discover_interfaces(self.device_snmp)
        self.assertIsNone(error)
        
        identifiers = [s['identifier'] for s in sensors]
        self.assertIn("snmp_traffic:1", identifiers)
        self.assertIn("snmp_numeric:1.3.6.1.2.1.25.3.3.1.2.1", identifiers)
        self.assertIn("snmp_numeric:1.3.6.1.4.1.14988.1.1.3.10.0", identifiers)
        self.assertIn("snmp_numeric:1.3.6.1.2.1.15.3.1.2.10.0.0.1", identifiers)

    @patch('monitor.services.MikrotikAPI')
    def test_check_mikrotik_api_advanced_sensors(self, mock_api_class):
        mock_api = MagicMock()
        mock_api.connect.return_value = True
        mock_api_class.return_value = mock_api
        
        t_cpu = MonitorTarget.objects.create(
            device=self.device_api,
            sensor_type='mikrotik_api',
            sensor_identifier='cpu:1',
            label='Core 1',
            host=self.device_api.host
        )
        t_volt = MonitorTarget.objects.create(
            device=self.device_api,
            sensor_type='mikrotik_api',
            sensor_identifier='health:voltage',
            label='Voltagem',
            host=self.device_api.host
        )
        t_bgp = MonitorTarget.objects.create(
            device=self.device_api,
            sensor_type='mikrotik_api',
            sensor_identifier='bgp:peer1',
            label='BGP Peer 1',
            host=self.device_api.host
        )

        # Mock check evaluations
        def mock_talk(sentence):
            if "/system/resource/cpu/print" in sentence[0]:
                return [['!re', '=cpu=1', '=load=15'], ['!done']]
            elif "/system/health/print" in sentence[0]:
                return [['!re', '=name=voltage', '=value=24.5'], ['!done']]
            elif "/routing/bgp/peer/print" in sentence[0]:
                return [['!re', '=name=peer1', '=state=established'], ['!done']]
            return []
        mock_api.talk.side_effect = mock_talk
        
        # Check CPU load
        PortCheckerService.check_target(t_cpu.id)
        t_cpu.refresh_from_db()
        self.assertEqual(t_cpu.sensor_value, "15%")
        
        # Check health voltage
        PortCheckerService.check_target(t_volt.id)
        t_volt.refresh_from_db()
        self.assertEqual(t_volt.sensor_value, "24.5 V")
        
        # Check BGP peer state established
        PortCheckerService.check_target(t_bgp.id)
        t_bgp.refresh_from_db()
        self.assertEqual(t_bgp.sensor_value, "Estabelecido")
        self.assertTrue(t_bgp.last_status)

    @patch('monitor.services.PortCheckerService._snmp_get')
    def test_check_snmp_scale_factor_and_bgp(self, mock_snmp_get):
        # 1. Test CPU temp scaling (450 -> 45.0ºC)
        t_temp = MonitorTarget.objects.create(
            device=self.device_snmp,
            sensor_type='snmp_numeric',
            sensor_identifier='1.3.6.1.4.1.14988.1.1.3.10.0',
            label='CPU Temp',
            host=self.device_snmp.host
        )
        mock_snmp_get.return_value = (True, "450")
        PortCheckerService.check_target(t_temp.id)
        t_temp.refresh_from_db()
        self.assertEqual(t_temp.sensor_value, "45.0ºC")
        
        # 2. Test BGP peer state mapping (established=6 -> Online)
        t_bgp = MonitorTarget.objects.create(
            device=self.device_snmp,
            sensor_type='snmp_numeric',
            sensor_identifier='1.3.6.1.2.1.15.3.1.2.10.0.0.1',
            label='BGP 10.0.0.1',
            host=self.device_snmp.host
        )
        mock_snmp_get.return_value = (True, "6")
        PortCheckerService.check_target(t_bgp.id)
        t_bgp.refresh_from_db()
        self.assertEqual(t_bgp.sensor_value, "Estabelecido")
        self.assertTrue(t_bgp.last_status)
        
        # BGP Active state (3 -> Offline)
        mock_snmp_get.return_value = (True, "3")
        PortCheckerService.check_target(t_bgp.id)
        t_bgp.refresh_from_db()
        self.assertEqual(t_bgp.sensor_value, "Active")
        self.assertFalse(t_bgp.last_status)
