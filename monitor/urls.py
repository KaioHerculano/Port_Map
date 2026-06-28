from django.urls import path
from . import views

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('add/', views.AddTargetsView.as_view(), name='add_targets'),
    path('target/<int:pk>/', views.TargetDetailView.as_view(), name='target_detail'),
    path('target/<int:pk>/toggle/', views.ToggleTargetView.as_view(), name='toggle_target'),
    path('target/<int:pk>/delete/', views.DeleteTargetView.as_view(), name='delete_target'),
    path('target/<int:pk>/edit/', views.UpdateTargetView.as_view(), name='edit_target'),
    path('target/<int:pk>/check/', views.TriggerCheckView.as_view(), name='trigger_check_single'),
    path('target/<int:pk>/test_telegram/', views.SendTestTelegramView.as_view(), name='send_test_telegram'),
    path('check-all/', views.TriggerCheckView.as_view(), name='trigger_check_all'),
    path('group/<int:group_id>/pdf/', views.GroupReportPDFView.as_view(), name='group_report_pdf'),
    path('group/<int:pk>/edit/', views.UpdateGroupView.as_view(), name='edit_group'),
    path('group/<int:pk>/delete/', views.DeleteGroupView.as_view(), name='delete_group'),
]
