from unittest.mock import MagicMock, patch

from django.test import TestCase
from faker import Faker

from monitor.models import Group, MonitorTarget
from monitor.utils import send_telegram_alert, send_telegram_message

fake = Faker()


class TelegramUtilsTests(TestCase):
    @patch("urllib.request.urlopen")
    def test_send_telegram_message_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"ok": true}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        dummy_token = fake.md5()
        dummy_chat_id = str(fake.random_number(digits=9))
        message_text = fake.sentence()

        with self.settings(
            TELEGRAM_BOT_TOKEN=dummy_token, TELEGRAM_CHAT_ID=dummy_chat_id
        ):
            res = send_telegram_message(message_text)

        self.assertTrue(res)
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_send_telegram_message_error_response(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = (
            b'{"ok": false, "description": "Unauthorized"}'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_response

        dummy_token = fake.md5()
        dummy_chat_id = str(fake.random_number(digits=9))
        message_text = fake.sentence()

        with self.settings(
            TELEGRAM_BOT_TOKEN=dummy_token, TELEGRAM_CHAT_ID=dummy_chat_id
        ):
            res = send_telegram_message(message_text)

        self.assertFalse(res)

    @patch("urllib.request.urlopen")
    def test_send_telegram_message_exception(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network timeout")

        dummy_token = fake.md5()
        dummy_chat_id = str(fake.random_number(digits=9))
        message_text = fake.sentence()

        with self.settings(
            TELEGRAM_BOT_TOKEN=dummy_token, TELEGRAM_CHAT_ID=dummy_chat_id
        ):
            res = send_telegram_message(message_text)

        self.assertFalse(res)

    def test_send_telegram_message_not_configured(self):
        message_text = fake.sentence()
        with self.settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID=""):
            res = send_telegram_message(message_text)
        self.assertFalse(res)

    @patch("monitor.utils.send_telegram_message")
    def test_send_telegram_alert_tcp_offline(self, mock_send_message):
        mock_send_message.return_value = True

        group = Group.objects.create(name=fake.company())
        target = MonitorTarget.objects.create(
            host=fake.ipv4(),
            port=fake.random_int(1, 65535),
            sensor_type="tcp",
            label=fake.word(),
            group=group,
            sensor_value=fake.word(),
        )

        res = send_telegram_alert(target, old_status=True, new_status=False)
        self.assertTrue(res)

        call_args = mock_send_message.call_args[0][0]
        self.assertIn("Sensor Offline / Falha!", call_args)
        self.assertIn("Porta:", call_args)
        self.assertIn(f"Grupo:</b> {group.name}", call_args)

    @patch("monitor.utils.send_telegram_message")
    def test_send_telegram_alert_ping_recovered_with_downtime(self, mock_send_message):
        mock_send_message.return_value = True

        target = MonitorTarget.objects.create(
            host=fake.ipv4(),
            sensor_type="ping",
            label=fake.word(),
            sensor_value=f"{fake.random_int(1, 100)}ms",
        )

        dummy_downtime = (
            f"{fake.random_int(1, 23):02d}:{fake.random_int(0, 59):02d}:"
            f"{fake.random_int(0, 59):02d}"
        )

        res = send_telegram_alert(
            target,
            old_status=False,
            new_status=True,
            downtime_duration=dummy_downtime,
        )
        self.assertTrue(res)

        call_args = mock_send_message.call_args[0][0]
        self.assertIn("Sensor Restabelecido!", call_args)
        self.assertIn("Tipo de Sensor:", call_args)
        self.assertIn(f"Tempo Offline:</b> <code>{dummy_downtime}</code>", call_args)
        self.assertIn("Grupo:</b> Sem grupo", call_args)
