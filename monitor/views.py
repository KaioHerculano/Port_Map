import json
import logging
from typing import Any, Dict, Optional, Union

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View, generic

from .forms import DeviceForm
from .models import AuditLog, Device, Group, MonitorLog, MonitorTarget
from .services import (
    DashboardService,
    DeviceService,
    GroupService,
    PortParserService,
    TargetService,
    TelegramService,
    log_audit,
)
from .tasks import check_single_target

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, generic.ListView):
    model = MonitorTarget
    template_name = "dashboard.html"
    context_object_name = "targets"
    paginate_by = 10

    def get_queryset(self):
        search_query = self.request.GET.get("q", "").strip()
        status_filter = self.request.GET.get("status", "").strip()
        group_id = self.request.GET.get("group", "").strip()
        return DashboardService.get_filtered_queryset(
            search_query, status_filter, group_id, company=self.request.user.company
        )

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        group_id = self.request.GET.get("group", "").strip()

        stats = DashboardService.get_dashboard_stats(
            group_id, company=self.request.user.company
        )
        context.update(stats)

        if group_id:
            context["selected_group"] = get_object_or_404(
                Group, pk=group_id, company=self.request.user.company
            )
        else:
            context["selected_group"] = None

        context["search_query"] = self.request.GET.get("q", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["group_id"] = group_id

        context["groups"] = GroupService.get_groups_with_stats(
            company=self.request.user.company
        )

        devices_qs = Device.objects.filter(
            company=self.request.user.company
        ).prefetch_related("sensors")
        if group_id:
            devices_qs = devices_qs.filter(group_id=group_id)
        context["devices"] = devices_qs

        context["recent_logs"] = (
            MonitorLog.objects.filter(target__company=self.request.user.company)
            .select_related("target", "target__group")
            .order_by("-timestamp")[:15]
        )

        if self.request.user.is_superuser:
            context["recent_audit_logs"] = (
                AuditLog.objects.filter(user__company=self.request.user.company)
                .select_related("user")
                .order_by("-timestamp")[:100]
            )
        else:
            context["recent_audit_logs"] = AuditLog.objects.none()

        return context


class AddTargetsView(LoginRequiredMixin, generic.TemplateView):
    template_name = "add_targets.html"

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["groups"] = Group.objects.filter(
            company=self.request.user.company
        ).order_by("name")
        return context

    def post(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseRedirect:
        import_type = request.POST.get("import_type", "single")

        if import_type == "group":
            group_name = request.POST.get("group_name", "").strip()
            if not group_name:
                messages.error(request, "O nome do grupo é obrigatório.")
                return redirect("add_targets")

            group, created = GroupService.create_group(
                group_name, request.user, company=request.user.company
            )
            if created:
                messages.success(
                    request, f"Grupo '{group_name}' cadastrado com sucesso!"
                )
            else:
                messages.info(request, f"O grupo '{group_name}' já existe.")
            return redirect("add_targets")

        check_interval_str = request.POST.get("check_interval", "60").strip()
        try:
            check_interval = int(check_interval_str)
        except ValueError:
            check_interval = 60

        telegram_alert_threshold_str = request.POST.get(
            "telegram_alert_threshold", "1"
        ).strip()
        try:
            telegram_alert_threshold = int(telegram_alert_threshold_str)
        except ValueError:
            telegram_alert_threshold = 1

        group_id = request.POST.get("group_id", "").strip()
        group = None
        if group_id:
            try:
                group = Group.objects.get(
                    pk=group_id, company=self.request.user.company
                )
            except Group.DoesNotExist:
                pass

        if import_type == "single":
            label = request.POST.get("label", "").strip()
            host = request.POST.get("host", "").strip()
            port_str = request.POST.get("port", "").strip()

            if not host or not port_str:
                messages.error(request, "Host e porta são obrigatórios.")
                return redirect("add_targets")

            try:
                port = int(port_str)
                if not (1 <= port <= 65535):
                    raise ValueError()
            except ValueError:
                messages.error(
                    request, "A porta deve ser um número inteiro entre 1 e 65535."
                )
                return redirect("add_targets")

            import_str = f"{host}:{port}"
            if label:
                import_str += f" [{label}]"

            created_targets, errors = PortParserService.parse_and_create_targets(
                import_str,
                group=group,
                check_interval=check_interval,
                telegram_alert_threshold=telegram_alert_threshold,
                company=request.user.company,
            )
            if created_targets:
                t = created_targets[0]
                log_audit(
                    user=request.user,
                    action="Criar",
                    model_name="Dispositivo",
                    object_repr=f"{t.label or t.host}:{t.port}",
                    changes=f"IP: {t.host}, Porta: {t.port}, Frequência: {t.check_interval}m, Regra de Alerta: {t.telegram_alert_threshold} falha(s), Grupo: {t.group.name if t.group else 'Nenhum'}",
                )
                check_single_target.delay(t.id)
                messages.success(
                    request, f"Alvo {t.host}:{t.port} cadastrado com sucesso!"
                )
            if errors:
                messages.error(request, errors[0])

        elif import_type == "bulk":
            bulk_text = request.POST.get("bulk_text", "").strip()
            if not bulk_text:
                messages.error(request, "Cole a lista de IPs e portas antes de enviar.")
                return redirect("add_targets")

            created_targets, errors = PortParserService.parse_and_create_targets(
                bulk_text,
                group=group,
                check_interval=check_interval,
                telegram_alert_threshold=telegram_alert_threshold,
                company=request.user.company,
            )

            if created_targets:
                for target in created_targets:
                    log_audit(
                        user=request.user,
                        action="Criar",
                        model_name="Dispositivo",
                        object_repr=f"{target.label or target.host}:{target.port}",
                        changes=f"IP: {target.host}, Porta: {target.port}, Frequência: {target.check_interval}m, Regra de Alerta: {target.telegram_alert_threshold} falha(s), Grupo: {target.group.name if target.group else 'Nenhum'} (lote)",
                    )
                    check_single_target.delay(target.id)
                messages.success(
                    request,
                    f"{len(created_targets)} alvos cadastrados/atualizados com sucesso!",
                )

            if errors:
                for error in errors:
                    messages.warning(request, error)

        return redirect("dashboard")


class TargetDetailView(LoginRequiredMixin, generic.DetailView):
    model = MonitorTarget
    template_name = "target_detail.html"
    context_object_name = "target"

    def get_queryset(self):
        return MonitorTarget.objects.filter(company=self.request.user.company)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        target = self.object

        TargetService.auto_correct_target_label(target)

        logs_list = target.logs.all().order_by("-timestamp")
        paginator = Paginator(logs_list, 30)
        page_number = self.request.GET.get("page")
        context["page_obj"] = paginator.get_page(page_number)

        start_date_str = self.request.GET.get("start_date", "").strip()
        end_date_str = self.request.GET.get("end_date", "").strip()
        period = self.request.GET.get("period", "").strip()

        from .services import TargetDetailService

        chart_ctx = TargetDetailService.get_chart_context(
            target, start_date_str, end_date_str, period
        )
        context.update(chart_ctx)

        context["uptime_24h"] = target.uptime_percentage_24h
        context["avg_latency"] = target.average_latency_24h

        sensor_type = target.sensor_type
        label_lower = (target.label or "").lower()
        ident_lower = (target.sensor_identifier or "").lower()

        if (
            sensor_type == "snmp_traffic"
            or ident_lower.startswith("traffic:")
            or "tráfego" in label_lower
            or "traffic" in label_lower
        ):
            chart_label = "Tráfego"
            chart_unit = "Mbps"
        elif (
            "temp" in label_lower
            or "temperatura" in label_lower
            or ident_lower == "temp"
            or "1.3.6.1.4.1.14988.1.1.3.10.0" in ident_lower
            or "1.3.6.1.4.1.14988.1.1.3.11.0" in ident_lower
            or "1.3.6.1.4.1.14988.1.1.3.9.0" in ident_lower
        ):
            chart_label = "Temperatura"
            chart_unit = "ºC"
        elif (
            "volt" in label_lower
            or "voltagem" in label_lower
            or "1.3.6.1.4.1.14988.1.1.3.8.0" in ident_lower
        ):
            chart_label = "Voltagem"
            chart_unit = "V"
        elif (
            "consumo" in label_lower
            or "power" in label_lower
            or "wates" in label_lower
            or "watts" in label_lower
            or "energia" in label_lower
            or "1.3.6.1.4.1.14988.1.1.3.12.0" in ident_lower
            or "1.3.6.1.4.1.14988.1.1.3.14.0" in ident_lower
        ):
            chart_label = "Consumo"
            chart_unit = "W"
        elif (
            "corrente" in label_lower
            or "current" in label_lower
            or "1.3.6.1.4.1.14988.1.1.3.13.0" in ident_lower
        ):
            chart_label = "Corrente"
            chart_unit = "A"
        elif (
            "cooler" in label_lower
            or "fan" in label_lower
            or "rotação" in label_lower
            or "rpm" in label_lower
            or "1.3.6.1.4.1.14988.1.1.3.17.0" in ident_lower
            or "1.3.6.1.4.1.14988.1.1.3.18.0" in ident_lower
        ):
            chart_label = "Rotação"
            chart_unit = "RPM"
        elif "psu" in label_lower or "estado da psu" in label_lower:
            chart_label = "Estado da Fonte"
            chart_unit = "Status"
        elif (
            "cpu" in label_lower
            or ident_lower == "cpu"
            or "1.3.6.1.2.1.25.3.3.1.2.1" in ident_lower
        ):
            chart_label = "Uso de CPU"
            chart_unit = "%"
        elif (
            "uptime" in label_lower
            or ident_lower == "uptime"
            or "1.3.6.1.2.1.1.3.0" in ident_lower
        ):
            chart_label = "Tempo de Atividade"
            chart_unit = "Dias"
        else:
            chart_label = "Latência"
            chart_unit = "ms"

        context["chart_label"] = chart_label
        context["chart_unit"] = chart_unit
        return context


class ToggleTargetView(LoginRequiredMixin, View):
    def post(
        self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any
    ) -> JsonResponse:
        target = get_object_or_404(MonitorTarget, pk=pk, company=request.user.company)
        is_active, message = TargetService.toggle_target(target, request.user)
        return JsonResponse(
            {"status": "success", "is_active": is_active, "message": message}
        )


class DeleteTargetView(LoginRequiredMixin, View):
    def post(
        self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any
    ) -> Union[JsonResponse, HttpResponseRedirect]:
        target = get_object_or_404(MonitorTarget, pk=pk, company=request.user.company)
        host_port = TargetService.delete_target(target, request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "status": "success",
                    "message": f"Alvo {host_port} excluído com sucesso.",
                }
            )
        messages.success(request, f"Alvo {host_port} excluído com sucesso.")
        return redirect("dashboard")


class TriggerCheckView(LoginRequiredMixin, View):
    def post(
        self, request: HttpRequest, pk: Optional[int] = None, *args: Any, **kwargs: Any
    ) -> JsonResponse:
        TargetService.trigger_manual_check(pk)
        message = (
            "Teste de porta concluído com sucesso."
            if pk
            else "Teste global de portas concluído com sucesso."
        )
        return JsonResponse({"status": "success", "message": message})


class UpdateTargetView(LoginRequiredMixin, generic.UpdateView):
    model = MonitorTarget
    context_object_name = "target"

    def get_queryset(self):
        return MonitorTarget.objects.filter(company=self.request.user.company)

    template_name = "update_target.html"
    fields = [
        "group",
        "label",
        "host",
        "port",
        "check_interval",
        "telegram_alert_threshold",
    ]
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        host = form.cleaned_data.get("host")
        port = form.cleaned_data.get("port")

        if (
            MonitorTarget.objects.filter(
                host=host, port=port, company=self.request.user.company
            )
            .exclude(pk=self.object.pk)
            .exists()
        ):
            form.add_error("port", "Este par de Host e Porta já está cadastrado.")
            return self.form_invalid(form)

        target = self.get_object()
        self.object, changes = TargetService.update_target(
            target, form.cleaned_data, self.request.user
        )
        messages.success(
            self.request,
            f"Alvo {self.object.host}:{self.object.port} updated successfully!",
        )
        return HttpResponseRedirect(self.get_success_url())


class GroupReportPDFView(LoginRequiredMixin, View):
    def get(
        self, request: HttpRequest, group_id: int, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        group = get_object_or_404(Group, pk=group_id, company=self.request.user.company)
        start_date_str = request.GET.get("start_date", "").strip()
        end_date_str = request.GET.get("end_date", "").strip()

        from .services import SLAReportService

        pdf_bytes, filename = SLAReportService.generate_pdf_report(
            group, start_date_str, end_date_str
        )

        if pdf_bytes is None:
            return HttpResponse("Erro ao gerar PDF", status=500)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'filename="{filename}"'
        return response


class UpdateGroupView(LoginRequiredMixin, View):
    def get(
        self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        group = get_object_or_404(Group, pk=pk, company=request.user.company)
        targets = group.targets.filter(device__isnull=True).order_by("label", "host")
        devices = Device.objects.filter(company=self.request.user.company).order_by(
            "name"
        )
        check_interval_choices = MonitorTarget._meta.get_field("check_interval").choices
        telegram_alert_threshold_choices = MonitorTarget._meta.get_field(
            "telegram_alert_threshold"
        ).choices

        context = {
            "group": group,
            "targets": targets,
            "devices": devices,
            "check_interval_choices": check_interval_choices,
            "telegram_alert_threshold_choices": telegram_alert_threshold_choices,
        }
        return render(request, "update_group.html", context)

    def post(
        self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any
    ) -> HttpResponseRedirect:
        group = get_object_or_404(Group, pk=pk, company=request.user.company)
        new_name = request.POST.get("name", "").strip()

        if not new_name:
            messages.error(request, "O nome do grupo não pode ser vazio.")
            return redirect("edit_group", pk=group.id)

        if Group.objects.filter(name__iexact=new_name).exclude(pk=pk).exists():
            messages.error(request, f"Já existe um grupo com o nome '{new_name}'.")
            return redirect("edit_group", pk=group.id)

        check_interval = request.POST.get("check_interval", "").strip()
        telegram_alert_threshold = request.POST.get(
            "telegram_alert_threshold", ""
        ).strip()
        selected_targets = request.POST.getlist("selected_targets")
        selected_devices = request.POST.getlist("selected_devices")

        from .services import GroupManagerService

        _, success_msg = GroupManagerService.update_group_settings(
            group,
            new_name,
            check_interval,
            telegram_alert_threshold,
            selected_targets,
            selected_devices,
            request.user,
        )

        messages.success(request, success_msg)
        return redirect("dashboard")


class DeleteGroupView(LoginRequiredMixin, View):
    def post(
        self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any
    ) -> HttpResponseRedirect:
        group = get_object_or_404(Group, pk=pk, company=request.user.company)
        GroupService.delete_group(group, request.user)
        messages.success(request, f"Grupo '{group.name}' excluído com sucesso.")
        return redirect("dashboard")


class SendTestTelegramView(LoginRequiredMixin, View):
    def post(
        self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any
    ) -> JsonResponse:
        target = get_object_or_404(MonitorTarget, pk=pk, company=request.user.company)
        success, error_msg = TelegramService.send_test_message(target)
        if success:
            return JsonResponse({"success": True})
        else:
            return JsonResponse({"success": False, "error": error_msg})


class TriggerMonthlyReportView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        from .tasks import send_monthly_telegram_report

        result = send_monthly_telegram_report.delay()
        return JsonResponse({"status": "success", "task_id": result.id})


class AddDeviceView(LoginRequiredMixin, generic.CreateView):
    model = Device
    form_class = DeviceForm
    template_name = "create_device.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        form.instance.company = self.request.user.company
        super().form_valid(form)
        device = self.object
        selected = self.request.POST.getlist("sensors")
        discovery_run = self.request.POST.get("discovery_run") == "true"

        created_count, is_discovery = DeviceService.create_device_with_sensors(
            device, selected, discovery_run, self.request.user
        )

        if is_discovery:
            messages.success(
                self.request,
                f"Equipamento '{device.name}' cadastrado com sucesso! {created_count} sensor(es) criado(s).",
            )
            return redirect("dashboard")

        messages.success(
            self.request,
            f"Equipamento '{device.name}' cadastrado com sucesso! {created_count} sensor(es) criado(s). Carregando auto-descoberta...",
        )

        for sensor in device.sensors.all():
            try:
                from .tasks import check_single_target

                check_single_target.delay(sensor.id)
            except Exception:
                pass

        return redirect("discover_sensors", pk=device.id)


class UpdateDeviceView(LoginRequiredMixin, generic.UpdateView):
    model = Device
    form_class = DeviceForm
    template_name = "update_device.html"
    success_url = reverse_lazy("dashboard")

    def get_queryset(self):
        return Device.objects.filter(company=self.request.user.company)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        device = self.get_object()

        from .services import DeviceDiscoveryService

        discovered_sensors, error = DeviceDiscoveryService.discover_interfaces(device)

        context["discovered_sensors_json"] = json.dumps(discovered_sensors)
        context["discovery_error"] = error
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        device = self.object

        selected_identifiers = self.request.POST.getlist("sensors")
        DeviceService.update_device_settings_and_sensors(
            device, selected_identifiers, self.request.user
        )

        messages.success(
            self.request, f"Equipamento '{device.name}' atualizado com sucesso!"
        )
        return response


class DeleteDeviceView(LoginRequiredMixin, View):
    def post(self, request, pk):
        device = get_object_or_404(Device, pk=pk, company=request.user.company)
        DeviceService.delete_device(device, request.user)
        messages.success(
            request,
            f"Equipamento '{device.name}' e todos os seus sensores associados foram excluídos com sucesso.",
        )
        return redirect("dashboard")


class DiscoverDeviceSensorsView(LoginRequiredMixin, View):
    def get(self, request, pk):
        device = get_object_or_404(Device, pk=pk, company=request.user.company)
        from .services import DeviceDiscoveryService

        interfaces, error = DeviceDiscoveryService.discover_interfaces(device)

        context = {"device": device, "interfaces": interfaces, "error": error}
        return render(request, "discover_sensors.html", context)

    def post(self, request, pk):
        device = get_object_or_404(Device, pk=pk, company=request.user.company)
        selected_identifiers = request.POST.getlist("selected_interfaces")

        from .services import DeviceDiscoveryService

        created_count = DeviceDiscoveryService.provision_sensors(
            device, selected_identifiers
        )

        messages.success(
            request,
            f"Sucesso! {created_count} novos sensores de tráfego foram adicionados ao equipamento '{device.name}'.",
        )
        return redirect("dashboard")


class StatusUpdateAPIView(LoginRequiredMixin, View):
    def get(self, request):
        targets_qs = MonitorTarget.objects.all().values(
            "id",
            "last_status",
            "sensor_value",
            "last_latency",
            "is_active",
            "sensor_type",
            "device_id",
            "group_id",
            "device__group_id",
        )
        devices_qs = Device.objects.filter(company=self.request.user.company).values(
            "id",
            "name",
            "host",
            "is_active",
            "group_id",
        )
        return JsonResponse(
            {
                "targets": list(targets_qs),
                "devices": list(devices_qs),
            }
        )


class DiscoverPreviewAPIView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        device_type = request.POST.get("device_type", "mikrotik_snmp")
        host = request.POST.get("host", "").strip()
        snmp_community = request.POST.get("snmp_community", "public").strip()
        snmp_port = request.POST.get("snmp_port", "161")

        try:
            snmp_port = int(snmp_port) if snmp_port else 161
        except ValueError:
            snmp_port = 161

        device = Device(
            device_type=device_type,
            host=host,
            snmp_community=snmp_community,
            snmp_port=snmp_port,
        )

        from .services import DeviceDiscoveryService

        sensors, error = DeviceDiscoveryService.discover_interfaces(device)

        return JsonResponse(
            {"success": error is None, "error": error, "sensors": sensors}
        )
