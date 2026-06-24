from django.urls import path
from . import views

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('add/', views.AddTargetsView.as_view(), name='add_targets'),
    path('target/<int:pk>/', views.TargetDetailView.as_view(), name='target_detail'),
    path('target/<int:pk>/toggle/', views.ToggleTargetView.as_view(), name='toggle_target'),
    path('target/<int:pk>/delete/', views.DeleteTargetView.as_view(), name='delete_target'),
    path('target/<int:pk>/check/', views.TriggerCheckView.as_view(), name='trigger_check_single'),
    path('check-all/', views.TriggerCheckView.as_view(), name='trigger_check_all'),
]
