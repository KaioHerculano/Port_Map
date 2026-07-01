from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.test import TestCase
from django.urls import reverse
from faker import Faker

from monitor.models import Group, MonitorLog, MonitorTarget

fake = Faker()


class MonitorViewTests(TestCase):
    def test_group_report_pdf_view(self):
        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        group = Group.objects.create(name=fake.company())
        target = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, group=group, last_status=True
        )
        MonitorLog.objects.create(target=target, status=True, latency=5.0)

        url = reverse("group_report_pdf", kwargs={"group_id": group.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(len(response.content) > 0)

    def test_group_stats_annotation_in_database(self):
        group = Group.objects.create(name=fake.company())

        MonitorTarget.objects.create(
            host=fake.ipv4(), port=5432, group=group, last_status=True, is_active=True
        )
        MonitorTarget.objects.create(
            host=fake.ipv4(), port=5432, group=group, last_status=False, is_active=True
        )
        MonitorTarget.objects.create(
            host=fake.ipv4(), port=5432, group=group, is_active=False
        )

        annotated_group = Group.objects.annotate(
            total_count=Count("targets"),
            online_count=Count(
                "targets", filter=Q(targets__last_status=True, targets__is_active=True)
            ),
            offline_count=Count(
                "targets", filter=Q(targets__last_status=False, targets__is_active=True)
            ),
            inactive_count=Count("targets", filter=Q(targets__is_active=False)),
        ).get(id=group.id)

        self.assertEqual(annotated_group.total_count, 3)
        self.assertEqual(annotated_group.online_count, 1)
        self.assertEqual(annotated_group.offline_count, 1)
        self.assertEqual(annotated_group.inactive_count, 1)

    def test_group_report_pdf_view_excludes_inactive_and_supports_custom_days(self):
        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        group = Group.objects.create(name=fake.company())

        MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, group=group, last_status=True, is_active=True
        )
        MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, group=group, last_status=True, is_active=False
        )

        url = reverse("group_report_pdf", kwargs={"group_id": group.id}) + "?days=7"
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(len(response.content) > 0)

    def test_update_group_view_get_and_post_batch(self):
        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        group = Group.objects.create(name=fake.company())
        target1 = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, group=group, check_interval=1
        )
        target2 = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, group=group, check_interval=1
        )

        url = reverse("edit_group", kwargs={"pk": group.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "monitor/update_group.html")
        self.assertEqual(response.context["group"], group)
        self.assertEqual(len(response.context["targets"]), 2)

        new_group_name = fake.company()
        post_data = {
            "name": new_group_name,
            "check_interval": "5",
            "selected_targets": [target1.id],
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        group.refresh_from_db()
        self.assertEqual(group.name, new_group_name)

        target1.refresh_from_db()
        target2.refresh_from_db()

        self.assertEqual(target1.check_interval, 5)
        self.assertEqual(target2.check_interval, 1)

    def test_delete_group_view_successful(self):
        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        group = Group.objects.create(name=fake.company())
        target = MonitorTarget.objects.create(host=fake.ipv4(), port=80, group=group)

        url = reverse("delete_group", kwargs={"pk": group.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Group.objects.filter(id=group.id).exists())

        target.refresh_from_db()
        self.assertIsNone(target.group)

    def test_target_detail_view_default_period_and_filtering(self):
        from datetime import timedelta

        from django.utils import timezone

        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        target = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, last_status=True
        )
        now = timezone.now()

        log1 = MonitorLog.objects.create(target=target, status=True, latency=10.0)
        MonitorLog.objects.filter(id=log1.id).update(timestamp=now - timedelta(hours=5))

        log2 = MonitorLog.objects.create(target=target, status=True, latency=15.0)
        MonitorLog.objects.filter(id=log2.id).update(timestamp=now - timedelta(days=5))

        url = reverse("target_detail", kwargs={"pk": target.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["chart_timestamps"]), 1)

        response = self.client.get(url + "?period=7d")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["chart_timestamps"]), 2)

        start_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
        response = self.client.get(
            url + f"?start_date={start_date}&end_date={end_date}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["chart_timestamps"]), 1)

    def test_send_test_telegram_successful(self):
        from unittest.mock import patch

        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        target = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, last_status=True
        )
        url = reverse("send_test_telegram", kwargs={"pk": target.id})

        with patch("monitor.utils.send_telegram_message") as mock_send_telegram_message:
            mock_send_telegram_message.return_value = True

            with self.settings(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="chat_id"):
                response = self.client.post(url)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"success": True})
            mock_send_telegram_message.assert_called_once()

    def test_trigger_monthly_report_view(self):
        from unittest.mock import MagicMock, patch

        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        url = reverse("trigger_monthly_report")
        with patch("monitor.tasks.send_monthly_telegram_report.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="mock_task_id")
            response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"status": "success", "task_id": "mock_task_id"}
        )
        mock_delay.assert_called_once()

    def test_audit_log_creation_on_toggle_and_delete(self):
        from monitor.models import AuditLog

        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        test_ip = fake.ipv4()
        target = MonitorTarget.objects.create(host=test_ip, port=80, is_active=True)

        toggle_url = reverse("toggle_target", kwargs={"pk": target.id})
        response = self.client.post(toggle_url)
        self.assertEqual(response.status_code, 200)

        audit1 = AuditLog.objects.filter(
            user=user, action="Desativar", model_name="Dispositivo"
        ).first()
        self.assertIsNotNone(audit1)
        self.assertIn(f"{test_ip}:80", audit1.object_repr)

        delete_url = reverse("delete_target", kwargs={"pk": target.id})
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 302)

        audit2 = AuditLog.objects.filter(
            user=user, action="Excluir", model_name="Dispositivo"
        ).first()
        self.assertIsNotNone(audit2)
        self.assertIn(f"{test_ip}:80", audit2.object_repr)

    def test_status_update_api_view_returns_devices_and_groups(self):
        from monitor.models import Device
        User = get_user_model()
        user = User.objects.create_user(
            username=fake.user_name(), password="testpassword", email=fake.email()
        )
        self.client.login(username=user.username, password="testpassword")

        group = Group.objects.create(name=fake.company())
        device = Device.objects.create(
            name=fake.name(), host=fake.ipv4(), device_type="generic_ping", group=group
        )
        target = MonitorTarget.objects.create(
            host=fake.ipv4(), port=80, group=group, device=device
        )

        url = reverse("api_status_update")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("targets", data)
        self.assertIn("devices", data)
        self.assertTrue(len(data["targets"]) >= 1)
        self.assertTrue(len(data["devices"]) >= 1)

        t_data = next(t for t in data["targets"] if t["id"] == target.id)
        self.assertEqual(t_data["device_id"], device.id)
        self.assertEqual(t_data["group_id"], group.id)
        self.assertEqual(t_data["device__group_id"], group.id)

        d_data = next(d for d in data["devices"] if d["id"] == device.id)
        self.assertEqual(d_data["group_id"], group.id)
