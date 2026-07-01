from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from faker import Faker

from monitor.models import DailySummary, MonitorLog, MonitorTarget
from monitor.tasks import (
    aggregate_daily_logs,
    check_all_targets,
    check_single_target,
    dispatch_scheduled_checks,
    send_monthly_telegram_report,
)

fake = Faker()


class MonitorTaskTests(TestCase):
    @patch("monitor.tasks.PortCheckerService.check_target")
    def test_check_single_target(self, mock_check):
        expected_res = fake.sentence()
        mock_check.return_value = expected_res
        dummy_id = fake.random_int(1, 100)

        res = check_single_target(dummy_id)
        self.assertEqual(res, expected_res)
        mock_check.assert_called_once_with(dummy_id)

    @patch("monitor.tasks.check_single_target.delay")
    def test_check_all_targets(self, mock_delay):
        # Create active and inactive targets
        t1 = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, check_interval=5, is_active=True
        )
        MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, check_interval=5, is_active=False
        )

        res = check_all_targets()
        self.assertIn("Dispatched check tasks for 1 targets", res)
        mock_delay.assert_called_once_with(t1.id)

    @patch("monitor.tasks.check_single_target.delay")
    def test_dispatch_scheduled_checks(self, mock_delay):
        now = timezone.now()
        MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, check_interval=15, is_active=True
        )
        MonitorTarget.objects.create(
            host=fake.ipv4(),
            port=80,
            check_interval=5,
            is_active=True,
            last_checked=now - timedelta(minutes=6),
        )
        # Inactive target
        MonitorTarget.objects.create(
            host=fake.ipv4(),
            port=80,
            check_interval=5,
            is_active=False,
            last_checked=now - timedelta(minutes=10),
        )

        res = dispatch_scheduled_checks()
        self.assertIn("Dispatched scheduled checks for 2 targets", res)
        self.assertEqual(mock_delay.call_count, 2)

    def test_send_monthly_telegram_report_not_configured(self):
        with self.settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID=""):
            res = send_monthly_telegram_report()
        self.assertEqual(res, "Telegram not configured. Monthly report skipped.")

    def test_send_monthly_telegram_report_no_sensors(self):
        with self.settings(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="123"):
            res = send_monthly_telegram_report()
        self.assertEqual(res, "No active sensors to report.")

    @patch("monitor.utils.send_telegram_message")
    def test_send_monthly_telegram_report_with_data_success(self, mock_send):
        mock_send.return_value = True

        # Target 1: Excelente (avail=90.0) -> TCP
        t1 = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, sensor_type="tcp", is_active=True
        )
        # Target 2: Bom (avail=75.0) -> Ping
        t2 = MonitorTarget.objects.create(
            host=fake.ipv4(), sensor_type="ping", is_active=True
        )
        # Target 3: Crítico / Low (avail=30.0) -> Ping (label is blank)
        t3 = MonitorTarget.objects.create(
            host=fake.ipv4(), sensor_type="ping", is_active=True, label=""
        )

        # Create logs for Target 1: 9 success, 1 fail
        for _ in range(9):
            MonitorLog.objects.create(target=t1, status=True, latency=10.0)
        MonitorLog.objects.create(target=t1, status=False, latency=0.0)

        # Create logs for Target 2: 3 success, 1 fail
        for _ in range(3):
            MonitorLog.objects.create(target=t2, status=True, latency=15.0)
        MonitorLog.objects.create(target=t2, status=False, latency=0.0)

        # Create logs for Target 3: 3 success, 7 fail
        for _ in range(3):
            MonitorLog.objects.create(target=t3, status=True, latency=20.0)
        for _ in range(7):
            MonitorLog.objects.create(target=t3, status=False, latency=0.0)

        # Shift logs to last month to make sure aggregate processes them
        now = timezone.now()
        first_day_current_month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        last_month_dt = first_day_current_month - timedelta(days=5)
        MonitorLog.objects.all().update(timestamp=last_month_dt)

        with self.settings(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="123"):
            res = send_monthly_telegram_report()

        self.assertIn("Monthly report sent successfully", res)
        mock_send.assert_called_once()
        message = mock_send.call_args[0][0]
        self.assertIn("Excelente (≥80%): 1", message)
        self.assertIn("Bom (70-79.9%): 1", message)
        self.assertIn("Crítico (&lt;70%): 1", message)

    @patch("monitor.utils.send_telegram_message")
    def test_send_monthly_telegram_report_send_failure(self, mock_send):
        mock_send.return_value = False

        t = MonitorTarget.objects.create(
            host=fake.ipv4(), sensor_type="ping", is_active=True
        )
        MonitorLog.objects.create(target=t, status=True, latency=10.0)

        now = timezone.now()
        first_day_current = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        MonitorLog.objects.all().update(timestamp=first_day_current - timedelta(days=5))

        with self.settings(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="123"):
            res = send_monthly_telegram_report()

        self.assertEqual(res, "Failed to send monthly report message.")

    @patch("monitor.utils.send_telegram_message")
    def test_send_monthly_telegram_report_bom_status(self, mock_send):
        mock_send.return_value = True

        t = MonitorTarget.objects.create(
            host=fake.ipv4(), sensor_type="ping", is_active=True
        )
        # Create logs resulting in 75% availability
        for _ in range(3):
            MonitorLog.objects.create(target=t, status=True, latency=10.0)
        MonitorLog.objects.create(target=t, status=False, latency=0.0)

        now = timezone.now()
        first_day_current = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        MonitorLog.objects.all().update(timestamp=first_day_current - timedelta(days=5))

        with self.settings(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_CHAT_ID="123"):
            res = send_monthly_telegram_report()

        self.assertIn("Monthly report sent successfully", res)
        mock_send.assert_called_once()
        message = mock_send.call_args[0][0]
        self.assertIn("Bom", message)

    def test_aggregate_daily_logs_with_data_and_empty_target(self):
        # Target with logs
        t1 = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, check_interval=5, is_active=True
        )
        # Target without logs
        t2 = MonitorTarget.objects.create(
            host=fake.ipv4(),
            port=80,
            check_interval=5,
            is_active=True,
            last_status=True,
        )

        yesterday = timezone.localdate() - timedelta(days=1)
        yesterday_dt = timezone.make_aware(
            datetime.combine(yesterday, datetime.min.time()) + timedelta(hours=12)
        )

        l1 = MonitorLog.objects.create(target=t1, status=True, latency=10.0)
        l2 = MonitorLog.objects.create(target=t1, status=False, latency=0.0)

        MonitorLog.objects.filter(id=l1.id).update(timestamp=yesterday_dt)
        MonitorLog.objects.filter(id=l2.id).update(
            timestamp=yesterday_dt + timedelta(minutes=5)
        )

        # Create old log that should be purged (older than 60 days)
        old_log = MonitorLog.objects.create(target=t1, status=True, latency=10.0)
        MonitorLog.objects.filter(id=old_log.id).update(
            timestamp=timezone.now() - timedelta(days=65)
        )

        res = aggregate_daily_logs()
        self.assertIn("Consolidated 2 daily summaries", res)
        self.assertIn("Purged 1 raw logs", res)

        s1 = DailySummary.objects.filter(target=t1, date=yesterday).first()
        self.assertIsNotNone(s1)
        self.assertEqual(s1.availability, 50.0)
        self.assertEqual(s1.avg_latency, 5.0)

        s2 = DailySummary.objects.filter(target=t2, date=yesterday).first()
        self.assertIsNotNone(s2)
        self.assertEqual(s2.availability, 100.0)
        self.assertEqual(s2.avg_latency, 0.0)
