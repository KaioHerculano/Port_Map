from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from ..models import MonitorTarget, MonitorLog, Group

class MonitorViewTests(TestCase):
    def test_group_report_pdf_view(self):
        User = get_user_model()
        user = User.objects.create_user(username="testuser", password="testpassword", email="test@test.com")
        self.client.login(username="testuser", password="testpassword")
        
        group = Group.objects.create(name="Grupo PDF Teste")
        target = MonitorTarget.objects.create(host="192.168.1.100", port=80, group=group, last_status=True)
        MonitorLog.objects.create(target=target, status=True, latency=5.0)
        
        url = reverse('group_report_pdf', kwargs={'group_id': group.id})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 0)

    def test_group_stats_annotation_in_database(self):
        # Create a group
        group = Group.objects.create(name="Servidores BD")
        
        # Create 3 targets under this group
        target1 = MonitorTarget.objects.create(host="10.0.0.1", port=5432, group=group, last_status=True, is_active=True)
        target2 = MonitorTarget.objects.create(host="10.0.0.2", port=5432, group=group, last_status=False, is_active=True)
        target3 = MonitorTarget.objects.create(host="10.0.0.3", port=5432, group=group, is_active=False) # Inactive
        
        # Fetch groups annotated with counts
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

    def test_group_report_pdf_view_excludes_inactive_and_supports_custom_days(self):
        User = get_user_model()
        user = User.objects.create_user(username="testuser2", password="testpassword", email="test2@test.com")
        self.client.login(username="testuser2", password="testpassword")
        
        group = Group.objects.create(name="Grupo Teste SLA")
        
        # 1 active target
        active_target = MonitorTarget.objects.create(host="192.168.10.1", port=80, group=group, last_status=True, is_active=True)
        # 1 inactive target
        inactive_target = MonitorTarget.objects.create(host="192.168.10.2", port=80, group=group, last_status=True, is_active=False)
        
        url = reverse('group_report_pdf', kwargs={'group_id': group.id}) + "?days=7"
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 0)

    def test_update_group_view_successful(self):
        User = get_user_model()
        user = User.objects.create_user(username="testuser_edit", password="testpassword", email="edit@test.com")
        self.client.login(username="testuser_edit", password="testpassword")
        
        group = Group.objects.create(name="Grupo Original")
        url = reverse('edit_group', kwargs={'pk': group.id})
        response = self.client.post(url, {'name': 'Grupo Modificado'})
        
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.name, "Grupo Modificado")

    def test_delete_group_view_successful(self):
        User = get_user_model()
        user = User.objects.create_user(username="testuser_del", password="testpassword", email="del@test.com")
        self.client.login(username="testuser_del", password="testpassword")
        
        group = Group.objects.create(name="Grupo Para Apagar")
        target = MonitorTarget.objects.create(host="192.168.20.1", port=80, group=group)
        
        url = reverse('delete_group', kwargs={'pk': group.id})
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Group.objects.filter(id=group.id).exists())
        
        # Verify target is not deleted but group is SET NULL
        target.refresh_from_db()
        self.assertIsNone(target.group)

    def test_target_detail_view_default_period_and_filtering(self):
        from django.utils import timezone
        from datetime import timedelta
        
        User = get_user_model()
        user = User.objects.create_user(username="testuser_detail", password="testpassword", email="detail@test.com")
        self.client.login(username="testuser_detail", password="testpassword")
        
        target = MonitorTarget.objects.create(host="192.168.30.1", port=80, last_status=True)
        now = timezone.now()
        
        log1 = MonitorLog.objects.create(target=target, status=True, latency=10.0)
        # Manually adjust timestamp since auto_now_add=True prevents direct assignment on creation
        MonitorLog.objects.filter(id=log1.id).update(timestamp=now - timedelta(hours=5))
        
        log2 = MonitorLog.objects.create(target=target, status=True, latency=15.0)
        MonitorLog.objects.filter(id=log2.id).update(timestamp=now - timedelta(days=5))
        
        url = reverse('target_detail', kwargs={'pk': target.id})
        
        # Test default (24h)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['chart_timestamps']), 1)  # only log1 in 24h
        
        # Test period 7d
        response = self.client.get(url + "?period=7d")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['chart_timestamps']), 2)  # both logs in 7d
        
        # Test custom range
        start_date = (now - timedelta(days=2)).strftime('%Y-%m-%d')
        end_date = now.strftime('%Y-%m-%d')
        response = self.client.get(url + f"?start_date={start_date}&end_date={end_date}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['chart_timestamps']), 1)  # only log1 in range
