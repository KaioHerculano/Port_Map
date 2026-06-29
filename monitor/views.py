import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import generic, View
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.urls import reverse_lazy
from django.utils import timezone
from datetime import timedelta

from .models import MonitorTarget, MonitorLog, Group, AuditLog, Device
from .forms import DeviceForm
from .services import PortParserService, log_audit
from .tasks import check_single_target, check_all_targets

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, generic.ListView):
    """Class-Based View to display targets dashboard with filters and stats."""
    model = MonitorTarget
    template_name = 'monitor/dashboard.html'
    context_object_name = 'targets'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset().select_related('group').filter(device__isnull=True)
        search_query = self.request.GET.get('q', '').strip()
        status_filter = self.request.GET.get('status', '').strip()
        group_id = self.request.GET.get('group', '').strip()

        if search_query:
            queryset = queryset.filter(
                Q(host__icontains=search_query) | 
                Q(label__icontains=search_query) | 
                Q(port__icontains=search_query) |
                Q(group__name__icontains=search_query)
            )

        if status_filter == 'online':
            queryset = queryset.filter(last_status=True, is_active=True)
        elif status_filter == 'offline':
            queryset = queryset.filter(last_status=False, is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)

        if group_id:
            queryset = queryset.filter(group_id=group_id)

        return queryset

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        all_targets = MonitorTarget.objects.filter(device__isnull=True)
        
        group_id = self.request.GET.get('group', '').strip()
        if group_id:
            all_targets = all_targets.filter(group_id=group_id)
            context['selected_group'] = get_object_or_404(Group, pk=group_id)
        else:
            context['selected_group'] = None

        context['total_count'] = all_targets.count()
        context['online_count'] = all_targets.filter(last_status=True, is_active=True).count()
        context['offline_count'] = all_targets.filter(last_status=False, is_active=True).count()
        context['inactive_count'] = all_targets.filter(is_active=False).count()

        context['search_query'] = self.request.GET.get('q', '')
        context['status_filter'] = self.request.GET.get('status', '')
        context['group_id'] = group_id

        # Fetch groups annotated with counts of standalone targets
        context['groups'] = Group.objects.annotate(
            total_count=Count('targets', filter=Q(targets__device__isnull=True)),
            online_count=Count('targets', filter=Q(targets__last_status=True, targets__is_active=True, targets__device__isnull=True)),
            offline_count=Count('targets', filter=Q(targets__last_status=False, targets__is_active=True, targets__device__isnull=True)),
            inactive_count=Count('targets', filter=Q(targets__is_active=False, targets__device__isnull=True))
        )
        
        # Fetch devices (annotated or with their prefetch sensors)
        devices_qs = Device.objects.all().prefetch_related('sensors')
        if group_id:
            devices_qs = devices_qs.filter(group_id=group_id)
        context['devices'] = devices_qs
        
        # Optimize query: select_related for related target model to avoid N+1
        context['recent_logs'] = MonitorLog.objects.select_related('target', 'target__group').order_by('-timestamp')[:15]
        
        # Fetch recent audit logs if user is superuser
        if self.request.user.is_superuser:
            context['recent_audit_logs'] = AuditLog.objects.select_related('user').order_by('-timestamp')[:100]
        else:
            context['recent_audit_logs'] = AuditLog.objects.none()
            
        return context


class AddTargetsView(LoginRequiredMixin, generic.TemplateView):
    """Class-Based View for target registration (individual or bulk)."""
    template_name = 'monitor/add_targets.html'

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['groups'] = Group.objects.all().order_by('name')
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseRedirect:
        import_type = request.POST.get('import_type', 'single')

        if import_type == 'group':
            group_name = request.POST.get('group_name', '').strip()
            if not group_name:
                messages.error(request, "O nome do grupo é obrigatório.")
                return redirect('add_targets')
            
            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                log_audit(
                    user=request.user,
                    action='Criar',
                    model_name='Grupo',
                    object_repr=group_name,
                    changes=f"Novo grupo '{group_name}' cadastrado"
                )
                messages.success(request, f"Grupo '{group_name}' cadastrado com sucesso!")
            else:
                messages.info(request, f"O grupo '{group_name}' já existe.")
            return redirect('add_targets')

        # Retrieve check_interval
        check_interval_str = request.POST.get('check_interval', '60').strip()
        try:
            check_interval = int(check_interval_str)
        except ValueError:
            check_interval = 60

        # Retrieve telegram_alert_threshold
        telegram_alert_threshold_str = request.POST.get('telegram_alert_threshold', '1').strip()
        try:
            telegram_alert_threshold = int(telegram_alert_threshold_str)
        except ValueError:
            telegram_alert_threshold = 1

        # Retrieve optional group selected in the dropdown
        group_id = request.POST.get('group_id', '').strip()
        group = None
        if group_id:
            try:
                group = Group.objects.get(pk=group_id)
            except Group.DoesNotExist:
                pass

        if import_type == 'single':
            label = request.POST.get('label', '').strip()
            host = request.POST.get('host', '').strip()
            port_str = request.POST.get('port', '').strip()

            if not host or not port_str:
                messages.error(request, "Host e porta são obrigatórios.")
                return redirect('add_targets')

            try:
                port = int(port_str)
                if not (1 <= port <= 65535):
                    raise ValueError()
            except ValueError:
                messages.error(request, "A porta deve ser um número inteiro entre 1 e 65535.")
                return redirect('add_targets')

            import_str = f"{host}:{port}"
            if label:
                import_str += f" [{label}]"

            created_targets, errors = PortParserService.parse_and_create_targets(
                import_str, 
                group=group, 
                check_interval=check_interval,
                telegram_alert_threshold=telegram_alert_threshold
            )
            if created_targets:
                t = created_targets[0]
                log_audit(
                    user=request.user,
                    action='Criar',
                    model_name='Dispositivo',
                    object_repr=f"{t.label or t.host}:{t.port}",
                    changes=f"IP: {t.host}, Porta: {t.port}, Frequência: {t.check_interval}m, Regra de Alerta: {t.telegram_alert_threshold} falha(s), Grupo: {t.group.name if t.group else 'Nenhum'}"
                )
                check_single_target.delay(t.id)
                messages.success(
                    request, 
                    f"Alvo {t.host}:{t.port} cadastrado com sucesso!"
                )
            if errors:
                messages.error(request, errors[0])

        elif import_type == 'bulk':
            bulk_text = request.POST.get('bulk_text', '').strip()
            if not bulk_text:
                messages.error(request, "Cole a lista de IPs e portas antes de enviar.")
                return redirect('add_targets')

            created_targets, errors = PortParserService.parse_and_create_targets(
                bulk_text, 
                group=group, 
                check_interval=check_interval,
                telegram_alert_threshold=telegram_alert_threshold
            )

            if created_targets:
                for target in created_targets:
                    log_audit(
                        user=request.user,
                        action='Criar',
                        model_name='Dispositivo',
                        object_repr=f"{target.label or target.host}:{target.port}",
                        changes=f"IP: {target.host}, Porta: {target.port}, Frequência: {target.check_interval}m, Regra de Alerta: {target.telegram_alert_threshold} falha(s), Grupo: {target.group.name if target.group else 'Nenhum'} (lote)"
                    )
                    check_single_target.delay(target.id)
                messages.success(
                    request, 
                    f"{len(created_targets)} alvos cadastrados/atualizados com sucesso!"
                )

            if errors:
                for error in errors:
                    messages.warning(request, error)

        return redirect('dashboard')


class TargetDetailView(LoginRequiredMixin, generic.DetailView):
    """Class-Based View to display full stats, latency graph, and check history."""
    model = MonitorTarget
    template_name = 'monitor/target_detail.html'
    context_object_name = 'target'

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        target = self.object

        # Retrieve connection history
        logs_list = target.logs.all().order_by('-timestamp')
        paginator = Paginator(logs_list, 30)
        page_number = self.request.GET.get('page')
        context['page_obj'] = paginator.get_page(page_number)

        start_date_str = self.request.GET.get('start_date', '').strip()
        end_date_str = self.request.GET.get('end_date', '').strip()
        period = self.request.GET.get('period', '').strip()

        from .services import TargetDetailService
        chart_ctx = TargetDetailService.get_chart_context(target, start_date_str, end_date_str, period)
        context.update(chart_ctx)
        
        context['uptime_24h'] = target.uptime_percentage_24h
        context['avg_latency'] = target.average_latency_24h

        # Determine chart label and unit
        sensor_type = target.sensor_type
        label_lower = (target.label or "").lower()
        
        if sensor_type == 'snmp_traffic' or (target.sensor_identifier or '').startswith('traffic:'):
            chart_label = "Tráfego"
            chart_unit = "Mbps"
        elif "temp" in label_lower or "temperatura" in label_lower or target.sensor_identifier == 'temp':
            chart_label = "Temperatura"
            chart_unit = "ºC"
        elif "cpu" in label_lower or target.sensor_identifier == 'cpu':
            chart_label = "Uso de CPU"
            chart_unit = "%"
        elif "uptime" in label_lower or target.sensor_identifier == 'uptime':
            chart_label = "Tempo de Atividade"
            chart_unit = "Dias"
        else:
            chart_label = "Latência"
            chart_unit = "ms"
            
        context['chart_label'] = chart_label
        context['chart_unit'] = chart_unit
        return context


class ToggleTargetView(LoginRequiredMixin, View):
    """Class-Based View to dynamically enable/disable monitoring of a target."""

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> JsonResponse:
        target = get_object_or_404(MonitorTarget, pk=pk)
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        logger.info("Estado do monitoramento alterado para %s:%d (Ativo: %s)", target.host, target.port, target.is_active)

        status_label = "ativado" if target.is_active else "desativado"
        log_audit(
            user=request.user,
            action='Ativar' if target.is_active else 'Desativar',
            model_name='Dispositivo',
            object_repr=f"{target.label or target.host}:{target.port}",
            changes=f"Alterado estado de atividade para: {status_label.capitalize()}"
        )

        return JsonResponse({
            'status': 'success',
            'is_active': target.is_active,
            'message': f"O monitoramento do alvo foi {status_label}."
        })


class DeleteTargetView(LoginRequiredMixin, View):
    """Class-Based View to remove target monitoring logs and metrics."""

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Union[JsonResponse, HttpResponseRedirect]:
        target = get_object_or_404(MonitorTarget, pk=pk)
        host_port = f"{target.host}:{target.port}"
        label_repr = f"{target.label or target.host}:{target.port}"
        group_name = target.group.name if target.group else 'Nenhum'
        target.delete()
        logger.info("Alvo de monitoramento excluido com sucesso: %s", host_port)

        log_audit(
            user=request.user,
            action='Excluir',
            model_name='Dispositivo',
            object_repr=label_repr,
            changes=f"Excluído monitoramento de {host_port}. Grupo: {group_name}"
        )

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'message': f"Alvo {host_port} excluído com sucesso."
            })

        messages.success(request, f"Alvo {host_port} excluído com sucesso.")
        return redirect('dashboard')


class TriggerCheckView(LoginRequiredMixin, View):
    """Class-Based View to trigger manual Celery check executions."""

    def post(self, request: HttpRequest, pk: Optional[int] = None, *args: Any, **kwargs: Any) -> JsonResponse:
        from .services import PortCheckerService
        if pk:
            target = get_object_or_404(MonitorTarget, pk=pk)
            try:
                PortCheckerService.check_target(target.id)
            except Exception as e:
                logger.error("Erro na checagem síncrona manual do alvo %d: %s", target.id, str(e))
                
            try:
                check_single_target.delay(target.id)
            except Exception:
                pass
            return JsonResponse({
                'status': 'success',
                'message': 'Teste de porta concluído com sucesso.'
            })
        else:
            active_targets = MonitorTarget.objects.filter(is_active=True)
            for t in active_targets:
                try:
                    PortCheckerService.check_target(t.id)
                except Exception as e:
                    logger.error("Erro na checagem síncrona manual global do alvo %d: %s", t.id, str(e))
            try:
                check_all_targets.delay()
            except Exception:
                pass
            return JsonResponse({
                'status': 'success',
                'message': 'Teste global de portas concluído com sucesso.'
            })


class UpdateTargetView(LoginRequiredMixin, generic.UpdateView):
    """Class-Based View to update target configurations."""
    model = MonitorTarget
    context_object_name = 'target'
    template_name = 'monitor/edit_target.html'
    fields = ['group', 'label', 'host', 'port', 'check_interval', 'telegram_alert_threshold']
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        host = form.cleaned_data.get('host')
        port = form.cleaned_data.get('port')
        
        # Check if another target already has this host and port
        if MonitorTarget.objects.filter(host=host, port=port).exclude(pk=self.object.pk).exists():
            form.add_error('port', 'Este par de Host e Porta já está cadastrado.')
            return self.form_invalid(form)

        # Get old values
        target = self.get_object()
        old_host = target.host
        old_port = target.port
        old_label = target.label
        old_interval = target.check_interval
        old_threshold = target.telegram_alert_threshold
        old_group = target.group.name if target.group else "Nenhum"

        response = super().form_valid(form)
        
        # Get new values
        target.refresh_from_db()
        new_host = target.host
        new_port = target.port
        new_label = target.label
        new_interval = target.check_interval
        new_threshold = target.telegram_alert_threshold
        new_group = target.group.name if target.group else "Nenhum"

        changes = []
        if old_host != new_host:
            changes.append(f"IP: {old_host} -> {new_host}")
        if old_port != new_port:
            changes.append(f"Porta: {old_port} -> {new_port}")
        if old_label != new_label:
            changes.append(f"Nome/Rótulo: {old_label or 'Vazio'} -> {new_label or 'Vazio'}")
        if old_interval != new_interval:
            changes.append(f"Frequência: {old_interval}m -> {new_interval}m")
        if old_threshold != new_threshold:
            changes.append(f"Regra de Alerta: {old_threshold} falha(s) -> {new_threshold} falha(s)")
        if old_group != new_group:
            changes.append(f"Grupo: {old_group} -> {new_group}")

        if changes:
            log_audit(
                user=self.request.user,
                action='Editar',
                model_name='Dispositivo',
                object_repr=f"{target.label or target.host}:{target.port}",
                changes="Alterações: " + ", ".join(changes)
            )

        messages.success(self.request, f"Alvo {self.object.host}:{self.object.port} updated successfully!")
        return response


class GroupReportPDFView(LoginRequiredMixin, View):
    """Class-Based View to generate a PDF report of target availability over the last 30 days."""
    
    def get(self, request: HttpRequest, group_id: int, *args: Any, **kwargs: Any) -> HttpResponse:
        group = get_object_or_404(Group, pk=group_id)
        start_date_str = request.GET.get('start_date', '').strip()
        end_date_str = request.GET.get('end_date', '').strip()
        
        from .services import SLAReportService
        pdf_bytes, filename = SLAReportService.generate_pdf_report(group, start_date_str, end_date_str)
        
        if pdf_bytes is None:
            return HttpResponse("Erro ao gerar PDF", status=500)
            
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'filename="{filename}"'
        return response


class UpdateGroupView(LoginRequiredMixin, View):
    """Class-Based View to update the group name and batch-edit check intervals of its targets."""
    
    def get(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        group = get_object_or_404(Group, pk=pk)
        targets = group.targets.all().order_by('label', 'host')
        
        # Get choices from MonitorTarget model field
        check_interval_choices = MonitorTarget._meta.get_field('check_interval').choices
        telegram_alert_threshold_choices = MonitorTarget._meta.get_field('telegram_alert_threshold').choices
        
        context = {
            'group': group,
            'targets': targets,
            'check_interval_choices': check_interval_choices,
            'telegram_alert_threshold_choices': telegram_alert_threshold_choices,
        }
        return render(request, 'monitor/edit_group.html', context)
        
    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponseRedirect:
        group = get_object_or_404(Group, pk=pk)
        new_name = request.POST.get('name', '').strip()
        
        if not new_name:
            messages.error(request, "O nome do grupo não pode ser vazio.")
            return redirect('edit_group', pk=group.id)
            
        if Group.objects.filter(name__iexact=new_name).exclude(pk=pk).exists():
            messages.error(request, f"Já existe um grupo com o nome '{new_name}'.")
            return redirect('edit_group', pk=group.id)
            
        # Batch update check intervals and telegram alert thresholds
        check_interval = request.POST.get('check_interval', '').strip()
        telegram_alert_threshold = request.POST.get('telegram_alert_threshold', '').strip()
        selected_targets = request.POST.getlist('selected_targets')
        
        from .services import GroupManagerService
        _, success_msg = GroupManagerService.update_group_settings(
            group, new_name, check_interval, telegram_alert_threshold, selected_targets, request.user
        )
        
        messages.success(request, success_msg)
        return redirect('dashboard')


class DeleteGroupView(LoginRequiredMixin, View):
    """Class-Based View to delete a group."""
    
    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponseRedirect:
        group = get_object_or_404(Group, pk=pk)
        group_name = group.name
        group.delete()
        log_audit(
            user=request.user,
            action='Excluir',
            model_name='Grupo',
            object_repr=group_name,
            changes=f"Grupo '{group_name}' excluído (dispositivos foram desassociados)"
        )
        messages.success(request, f"Grupo '{group_name}' excluído com sucesso.")
        return redirect('dashboard')


class SendTestTelegramView(LoginRequiredMixin, View):
    """View to send a manual test Telegram message for a specific target."""
    
    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> JsonResponse:
        from django.conf import settings
        from django.utils import timezone
        
        target = get_object_or_404(MonitorTarget, pk=pk)
        
        # Check if Telegram is configured
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
        
        if not token or not chat_id:
            return JsonResponse({'success': False, 'error': 'Telegram não está configurado no arquivo .env.'})
            
        label = target.label or "Sem identificação"
        group_name = target.group.name if target.group else "Sem grupo"
        local_time = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S')
        message = (
            f"<b>Teste de Notificação Manual</b>\n\n"
            f"<b>Dispositivo:</b> {label}\n"
            f"<b>IP:</b> <code>{target.host}</code>\n"
            f"<b>Porta:</b> <code>{target.port}</code>\n"
            f"<b>Grupo:</b> {group_name}\n"
            f"<b>Horário:</b> {local_time}"
        )
        
        from .utils import send_telegram_message
        success = send_telegram_message(message)
        
        if success:
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Falha ao entregar a mensagem no Telegram. Verifique o Token e o Chat ID.'})


class TriggerMonthlyReportView(LoginRequiredMixin, View):
    """View to manually trigger the monthly SLA report Celery task."""
    
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        from .tasks import send_monthly_telegram_report
        # Trigger task asynchronously in Celery
        result = send_monthly_telegram_report.delay()
        return JsonResponse({'status': 'success', 'task_id': result.id})


class AddDeviceView(LoginRequiredMixin, generic.CreateView):
    model = Device
    form_class = DeviceForm
    template_name = 'monitor/add_device.html'
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        response = super().form_valid(form)
        device = self.object
        # Auto-create default sensors
        if device.device_type == 'generic_ping':
            target, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='ping',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Ping', 'check_interval': 1, 'is_active': True}
            )
            if not created and not target.is_active:
                target.is_active = True
                target.save(update_fields=['is_active'])
        elif device.device_type == 'mikrotik':
            # Ping
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='ping',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Ping', 'check_interval': 1, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            # CPU
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='mikrotik_api',
                sensor_identifier='cpu',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - CPU', 'check_interval': 1, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            # Temp
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='mikrotik_api',
                sensor_identifier='temp',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Temp', 'check_interval': 5, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            # Uptime
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='mikrotik_api',
                sensor_identifier='uptime',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Uptime', 'check_interval': 15, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
        elif device.device_type == 'mikrotik_snmp':
            # Ping
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='ping',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Ping', 'check_interval': 1, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            # CPU
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='snmp_numeric',
                sensor_identifier='1.3.6.1.2.1.25.3.3.1.2.1',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - CPU', 'check_interval': 1, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            # Temp
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='snmp_numeric',
                sensor_identifier='1.3.6.1.4.1.14988.1.1.3.10.0',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Temp CPU', 'check_interval': 5, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            # Uptime
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='snmp_numeric',
                sensor_identifier='1.3.6.1.2.1.1.3.0',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Uptime', 'check_interval': 15, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
        elif device.device_type == 'parks_olt':
            # Ping
            t, created = MonitorTarget.objects.get_or_create(
                device=device,
                sensor_type='ping',
                host=device.host,
                group=device.group,
                defaults={'label': f'{device.name} - Ping', 'check_interval': 1, 'is_active': True}
            )
            if not created and not t.is_active:
                t.is_active = True
                t.save(update_fields=['is_active'])
            
        log_audit(
            user=self.request.user,
            action='Criar',
            model_name='Equipamento',
            object_repr=device.name,
            changes=f"Novo equipamento cadastrado. Nome: {device.name}, Tipo: {device.device_type}, IP: {device.host}"
        )
        messages.success(self.request, f"Equipamento '{device.name}' cadastrado com sucesso! Sensores padrão criados.")
        
        # Trigger checks for all new sensors immediately and synchronously
        from .services import PortCheckerService
        for sensor in device.sensors.all():
            try:
                PortCheckerService.check_target(sensor.id)
            except Exception as e:
                logger.error("Erro na checagem inicial síncrona do alvo %d: %s", sensor.id, str(e))
            
            try:
                from .tasks import check_single_target
                check_single_target.delay(sensor.id)
            except Exception:
                pass
            
        return response


class UpdateDeviceView(LoginRequiredMixin, generic.UpdateView):
    model = Device
    form_class = DeviceForm
    template_name = 'monitor/edit_device.html'
    success_url = reverse_lazy('dashboard')

    def form_valid(self, form):
        response = super().form_valid(form)
        device = self.object
        log_audit(
            user=self.request.user,
            action='Editar',
            model_name='Equipamento',
            object_repr=device.name,
            changes=f"Alterado configurações do equipamento ID {device.id}"
        )
        messages.success(self.request, f"Equipamento '{device.name}' atualizado com sucesso!")
        return response


class DeleteDeviceView(LoginRequiredMixin, View):
    def post(self, request, pk):
        device = get_object_or_404(Device, pk=pk)
        device_name = device.name
        device.delete()
        log_audit(
            user=request.user,
            action='Excluir',
            model_name='Equipamento',
            object_repr=device_name,
            changes=f"Equipamento '{device_name}' e seus sensores foram excluídos"
        )
        messages.success(request, f"Equipamento '{device_name}' e todos os seus sensores associados foram excluídos com sucesso.")
        return redirect('dashboard')


class DiscoverDeviceSensorsView(LoginRequiredMixin, View):
    def get(self, request, pk):
        device = get_object_or_404(Device, pk=pk)
        from .services import DeviceDiscoveryService
        interfaces, error = DeviceDiscoveryService.discover_interfaces(device)

        context = {
            'device': device,
            'interfaces': interfaces,
            'error': error
        }
        return render(request, 'monitor/discover_sensors.html', context)

    def post(self, request, pk):
        device = get_object_or_404(Device, pk=pk)
        selected_identifiers = request.POST.getlist('selected_interfaces')
        
        from .services import DeviceDiscoveryService
        created_count = DeviceDiscoveryService.provision_sensors(device, selected_identifiers)

        messages.success(request, f"Sucesso! {created_count} novos sensores de tráfego foram adicionados ao equipamento '{device.name}'.")
        return redirect('dashboard')
