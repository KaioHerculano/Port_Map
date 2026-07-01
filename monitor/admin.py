from django.contrib import admin

from .models import AuditLog, DailySummary, Group, MonitorLog, MonitorTarget


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(MonitorTarget)
class MonitorTargetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "host",
        "port",
        "label",
        "group",
        "check_interval",
        "telegram_alert_threshold",
        "is_active",
        "last_status",
        "last_checked",
    )
    list_filter = (
        "is_active",
        "last_status",
        "check_interval",
        "telegram_alert_threshold",
        "group",
    )
    search_fields = ("host", "label")
    ordering = ("group", "host", "port")
    list_editable = ("is_active", "check_interval", "telegram_alert_threshold")


@admin.register(MonitorLog)
class MonitorLogAdmin(admin.ModelAdmin):
    list_display = ("id", "target", "status", "latency", "timestamp")
    list_filter = ("status", "timestamp", "target__group")
    search_fields = ("target__host", "target__label")
    ordering = ("-timestamp",)
    readonly_fields = ("target", "status", "latency", "timestamp")


@admin.register(DailySummary)
class DailySummaryAdmin(admin.ModelAdmin):
    list_display = ("id", "target", "date", "availability", "avg_latency")
    list_filter = ("date", "target__group")
    search_fields = ("target__host", "target__label")
    ordering = ("-date", "target")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "action", "model_name", "object_repr", "timestamp")
    list_filter = ("action", "model_name", "timestamp", "user")
    search_fields = ("object_repr", "changes", "user__username")
    ordering = ("-timestamp",)
    readonly_fields = (
        "user",
        "action",
        "model_name",
        "object_repr",
        "changes",
        "timestamp",
    )
