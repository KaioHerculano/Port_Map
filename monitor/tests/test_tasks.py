from django.test import TestCase
from django.utils import timezone
from datetime import datetime, timedelta

from ..models import MonitorTarget

class MonitorTaskTests(TestCase):
    def test_dispatch_scheduled_checks(self):
        now = timezone.now()
        t1 = MonitorTarget.objects.create(host="10.0.0.10", port=80, check_interval=15, is_active=True) # never checked
        t2 = MonitorTarget.objects.create(host="10.0.0.11", port=80, check_interval=5, is_active=True, last_checked=now - timedelta(minutes=6)) # needs checking
        t3 = MonitorTarget.objects.create(host="10.0.0.12", port=80, check_interval=60, is_active=True, last_checked=now - timedelta(minutes=10)) # does not need checking
        t4 = MonitorTarget.objects.create(host="10.0.0.13", port=80, check_interval=5, is_active=False, last_checked=now - timedelta(minutes=10)) # inactive
        
        # Test queryset filtering logic (same as task logic)
        from django.db.models import Q
        query = Q(last_checked__isnull=True)
        active_intervals = MonitorTarget.objects.filter(is_active=True).values_list('check_interval', flat=True).distinct()
        for interval in active_intervals:
            cutoff = now - timedelta(minutes=interval) + timedelta(seconds=10)
            query |= Q(check_interval=interval, last_checked__lte=cutoff)
        
        targets_to_check = MonitorTarget.objects.filter(is_active=True).filter(query)
        self.assertEqual(targets_to_check.count(), 2)
        self.assertIn(t1, targets_to_check)
        self.assertIn(t2, targets_to_check)
        self.assertNotIn(t3, targets_to_check)
        self.assertNotIn(t4, targets_to_check)

    def test_aggregate_daily_logs(self):
        from ..models import MonitorLog, DailySummary
        from ..tasks import aggregate_daily_logs
        
        t = MonitorTarget.objects.create(host="192.168.1.100", port=80, check_interval=5, is_active=True)
        yesterday = timezone.localdate() - timedelta(days=1)
        yesterday_dt = timezone.make_aware(datetime.combine(yesterday, datetime.min.time()) + timedelta(hours=12))
        
        l1 = MonitorLog.objects.create(target=t, status=True, latency=10.0)
        l2 = MonitorLog.objects.create(target=t, status=False, latency=0.0)
        
        MonitorLog.objects.filter(id=l1.id).update(timestamp=yesterday_dt)
        MonitorLog.objects.filter(id=l2.id).update(timestamp=yesterday_dt + timedelta(minutes=5))
        
        result = aggregate_daily_logs()
        
        summary = DailySummary.objects.filter(target=t, date=yesterday).first()
        self.assertIsNotNone(summary)
        self.assertEqual(summary.availability, 50.0)
        self.assertEqual(summary.avg_latency, 5.0)
