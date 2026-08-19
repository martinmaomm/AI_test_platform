from django.urls import path
from . import views

urlpatterns = [
    path('summary/', views.DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('trend/', views.DashboardTrendView.as_view(), name='dashboard-trend'),
    path('top-failures/', views.DashboardTopFailuresView.as_view(), name='dashboard-top-failures'),
]
