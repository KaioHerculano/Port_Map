import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import generic, View
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse_lazy

from .models import MonitorTarget, MonitorLog
from .services import PortParserService
from .tasks import check_single_target, check_all_targets

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, generic.ListView):
    """Class-Based View to display targets dashboard with filters and stats."""
    model = MonitorTarget
    template_name = 'monitor/dashboard.html'
    context_object_name = 'targets'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        search_query = self.request.GET.get('q', '').strip()
        status_filter = self.request.GET.get('status', '').strip()

        if search_query:
            queryset = queryset.filter(
                Q(host__icontains=search_query) | 
                Q(label__icontains=search_query) | 
                Q(port__icontains=search_query)
            )

        if status_filter == 'online':
            queryset = queryset.filter(last_status=True, is_active=True)
        elif status_filter == 'offline':
            queryset = queryset.filter(last_status=False, is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)

        return queryset

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        all_targets = MonitorTarget.objects.all()

        context['total_count'] = all_targets.count()
        context['online_count'] = all_targets.filter(last_status=True, is_active=True).count()
        context['offline_count'] = all_targets.filter(last_status=False, is_active=True).count()
        context['inactive_count'] = all_targets.filter(is_active=False).count()

        context['search_query'] = self.request.GET.get('q', '')
        context['status_filter'] = self.request.GET.get('status', '')
        
        # Optimize query: select_related for related target model to avoid N+1
        context['recent_logs'] = MonitorLog.objects.select_related('target').order_by('-timestamp')[:15]
        return context


class AddTargetsView(LoginRequiredMixin, generic.TemplateView):
    """Class-Based View for target registration (individual or bulk)."""
    template_name = 'monitor/add_targets.html'

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseRedirect:
        import_type = request.POST.get('import_type', 'single')

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

            created_targets, errors = PortParserService.parse_and_create_targets(import_str)
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

            created_targets, errors = PortParserService.parse_and_create_targets(bulk_text)

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

        # Build datasets for Chart.js (Last 50 logs chronologically)
        chart_logs = list(target.logs.all()[:50])
        chart_logs.reverse()

        context['chart_timestamps'] = [log.timestamp.strftime('%H:%M:%S') for log in chart_logs]
        context['chart_latencies'] = [log.latency if log.status else 0 for log in chart_logs]
        context['chart_statuses'] = [1 if log.status else 0 for log in chart_logs]
        
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
