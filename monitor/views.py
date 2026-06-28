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

from .models import MonitorTarget, MonitorLog, Group
from .services import PortParserService
from .tasks import check_single_target, check_all_targets

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, generic.ListView):
    """Class-Based View to display targets dashboard with filters and stats."""
    model = MonitorTarget
    template_name = 'monitor/dashboard.html'
    context_object_name = 'targets'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset().select_related('group')
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
        all_targets = MonitorTarget.objects.all()
        
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

        # Fetch groups annotated with counts
        context['groups'] = Group.objects.annotate(
            total_count=Count('targets'),
            online_count=Count('targets', filter=Q(targets__last_status=True, targets__is_active=True)),
            offline_count=Count('targets', filter=Q(targets__last_status=False, targets__is_active=True)),
            inactive_count=Count('targets', filter=Q(targets__is_active=False))
        )
        
        # Optimize query: select_related for related target model to avoid N+1
        context['recent_logs'] = MonitorLog.objects.select_related('target', 'target__group').order_by('-timestamp')[:15]
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
                check_single_target.delay(created_targets[0].id)
                messages.success(
                    request, 
                    f"Alvo {created_targets[0].host}:{created_targets[0].port} cadastrado com sucesso!"
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

        # Build datasets for Chart.js
        now = timezone.now()
        start_date_str = self.request.GET.get('start_date', '').strip()
        end_date_str = self.request.GET.get('end_date', '').strip()
        period = self.request.GET.get('period', '').strip()
        
        import datetime
        from django.utils.dateparse import parse_date
        
        start_date_val = None
        end_date_val = None
        
        if start_date_str and end_date_str:
            start_date_val = parse_date(start_date_str)
            end_date_val = parse_date(end_date_str)
            
        if start_date_val and end_date_val:
            start_dt = timezone.make_aware(datetime.datetime.combine(start_date_val, datetime.time.min))
            end_dt = timezone.make_aware(datetime.datetime.combine(end_date_val, datetime.time.max))
            period = 'custom'
        else:
            if not period:
                period = '24h'
            if period == '7d':
                start_dt = now - timedelta(days=7)
                start_date_str = (now - timedelta(days=7)).strftime('%Y-%m-%d')
            elif period == '30d':
                start_dt = now - timedelta(days=30)
                start_date_str = (now - timedelta(days=30)).strftime('%Y-%m-%d')
            else: # '24h'
                start_dt = now - timedelta(days=1)
                period = '24h'
                start_date_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
            end_dt = now
            end_date_str = now.strftime('%Y-%m-%d')

        # Filter logs in range
        chart_logs_query = target.logs.filter(timestamp__gte=start_dt, timestamp__lte=end_dt).order_by('timestamp')
        
        # Downsample if log count exceeds 300 to optimize performance
        log_count = chart_logs_query.count()
        if log_count > 300:
            step = log_count // 300
            chart_logs = list(chart_logs_query)[::step]
        else:
            chart_logs = list(chart_logs_query)

        # Dynamic format for X-axis labels based on duration
        if (end_dt - start_dt) > timedelta(days=1):
            timestamp_format = '%d/%m %H:%M'
        else:
            timestamp_format = '%H:%M:%S'

        context['chart_timestamps'] = [timezone.localtime(log.timestamp).strftime(timestamp_format) for log in chart_logs]
        context['chart_latencies'] = [log.latency if log.status else 0 for log in chart_logs]
        context['chart_statuses'] = [1 if log.status else 0 for log in chart_logs]
        
        context['period'] = period
        context['start_date'] = start_date_str
        context['end_date'] = end_date_str
        
        context['uptime_24h'] = target.uptime_percentage_24h
        context['avg_latency'] = target.average_latency_24h
        return context


class ToggleTargetView(LoginRequiredMixin, View):
    """Class-Based View to dynamically enable/disable monitoring of a target."""

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> JsonResponse:
        target = get_object_or_404(MonitorTarget, pk=pk)
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        logger.info("Estado do monitoramento alterado para %s:%d (Ativo: %s)", target.host, target.port, target.is_active)

        status_label = "ativado" if target.is_active else "desativado"
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
        target.delete()
        logger.info("Alvo de monitoramento excluido com sucesso: %s", host_port)

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
        if pk:
            target = get_object_or_404(MonitorTarget, pk=pk)
            task = check_single_target.delay(target.id)
            logger.info("Agendamento manual de varredura unica para %s:%d (Task ID: %s)", target.host, target.port, task.id)
            return JsonResponse({
                'status': 'success',
                'task_id': task.id,
                'message': 'Teste de porta iniciado em segundo plano.'
            })
        else:
            task = check_all_targets.delay()
            logger.info("Agendamento manual de varredura global para todos os alvos ativos (Task ID: %s)", task.id)
            return JsonResponse({
                'status': 'success',
                'task_id': task.id,
                'message': 'Teste global de portas iniciado em segundo plano.'
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

        response = super().form_valid(form)
        messages.success(self.request, f"Alvo {self.object.host}:{self.object.port} atualizado com sucesso!")
        return response


class GroupReportPDFView(LoginRequiredMixin, View):
    """Class-Based View to generate a PDF report of target availability over the last 30 days."""
    
    def get(self, request: HttpRequest, group_id: int, *args: Any, **kwargs: Any) -> HttpResponse:
        group = get_object_or_404(Group, pk=group_id)
        
        # Retrieve custom start and end dates (defaulting to last 30 days)
        start_date_str = request.GET.get('start_date', '').strip()
        end_date_str = request.GET.get('end_date', '').strip()
        
        from django.utils import timezone
        from django.utils.dateparse import parse_date
        from datetime import timedelta
        import datetime
        from django.db.models import Count, Q, Case, When, Value, FloatField, F
        from django.db.models.functions import Cast
        
        # Fallbacks
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30)
        
        if start_date_str:
            parsed_start = parse_date(start_date_str)
            if parsed_start:
                start_date = timezone.make_aware(datetime.datetime.combine(parsed_start, datetime.time.min))
                
        if end_date_str:
            parsed_end = parse_date(end_date_str)
            if parsed_end:
                end_date = timezone.make_aware(datetime.datetime.combine(parsed_end, datetime.time.max))
        
        # Calculate availability over the specified range for each active target in this group
        targets = group.targets.filter(is_active=True).annotate(
            total_logs=Count('logs', filter=Q(logs__timestamp__gte=start_date, logs__timestamp__lte=end_date)),
            success_logs=Count('logs', filter=Q(logs__timestamp__gte=start_date, logs__timestamp__lte=end_date, logs__status=True))
        ).annotate(
            availability=Case(
                When(total_logs=0, then=Case(
                    When(last_status=True, then=Value(100.0)),
                    default=Value(0.0)
                )),
                default=Cast(F('success_logs') * 100.0 / F('total_logs'), output_field=FloatField())
            )
        ).order_by('host', 'port')
        
        # Round availability to 1 decimal place, calculate average, and pair targets for 2-column layout
        total_availability = 0.0
        target_count = len(targets)
        
        for target in targets:
            target.availability_rounded = round(target.availability, 1)
            total_availability += target.availability_rounded
            
        # Pair targets side-by-side (cols = 2)
        paired_targets = []
        cols = 2
        for i in range(0, len(targets), cols):
            chunk = list(targets[i:i + cols])
            while len(chunk) < cols:
                chunk.append(None)
            paired_targets.append(chunk)
            
        group_availability = round(total_availability / target_count, 1) if target_count > 0 else 100.0
        
        # Calculate how many days are in the period
        delta_days = (end_date - start_date).days
        if delta_days <= 0:
            delta_days = 1
        
        # Render HTML template for the PDF report
        from django.template.loader import render_to_string
        from xhtml2pdf import pisa
        import io
        
        context = {
            'group': group,
            'paired_targets': paired_targets,
            'group_availability': group_availability,
            'target_count': target_count,
            'start_date': start_date,
            'end_date': end_date,
            'period_days': delta_days,
            'generated_at': timezone.now(),
        }
        
        html_string = render_to_string('monitor/group_report_pdf.html', context)
        
        # Create PDF
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer)
        
        if pisa_status.err:
            return HttpResponse("Erro ao gerar PDF", status=500)
            
        pdf_buffer.seek(0)
        response = HttpResponse(pdf_buffer, content_type='application/pdf')
        
        # Format filename to be safe
        safe_name = "".join([c if c.isalnum() else "_" for c in group.name.lower()])
        response['Content-Disposition'] = f'filename="relatorio_sla_{safe_name}.pdf"'
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
            
        old_name = group.name
        group.name = new_name
        group.save()
        
        # Batch update check intervals and telegram alert thresholds
        check_interval = request.POST.get('check_interval', '').strip()
        telegram_alert_threshold = request.POST.get('telegram_alert_threshold', '').strip()
        selected_targets = request.POST.getlist('selected_targets')
        
        success_msg = f"Grupo '{old_name}' renomeado para '{new_name}' com sucesso."
        
        if selected_targets:
            if check_interval:
                try:
                    interval_val = int(check_interval)
                    updated_count = MonitorTarget.objects.filter(
                        id__in=selected_targets, 
                        group=group
                    ).update(check_interval=interval_val)
                    success_msg += f" Frequência de verificação atualizada em {updated_count} dispositivo(s)."
                except ValueError:
                    messages.error(request, "Intervalo de verificação inválido.")
            
            if telegram_alert_threshold:
                try:
                    threshold_val = int(telegram_alert_threshold)
                    updated_count = MonitorTarget.objects.filter(
                        id__in=selected_targets, 
                        group=group
                    ).update(telegram_alert_threshold=threshold_val)
                    success_msg += f" Regra de alerta do Telegram atualizada em {updated_count} dispositivo(s)."
                except ValueError:
                    messages.error(request, "Regra de alerta do Telegram inválida.")
                
        messages.success(request, success_msg)
        return redirect('dashboard')


class DeleteGroupView(LoginRequiredMixin, View):
    """Class-Based View to delete a group."""
    
    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponseRedirect:
        group = get_object_or_404(Group, pk=pk)
        group_name = group.name
        group.delete()
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
            f"🔔 <b>Teste de Notificação Manual</b>\n\n"
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
