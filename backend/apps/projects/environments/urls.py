from django.urls import path
from . import views

app_name = 'environments'

urlpatterns = [
    # 环境管理API
    path('', views.EnvironmentListView.as_view(), name='environment_list'),
    path('<int:pk>/', views.EnvironmentDetailView.as_view(), name='environment_detail'),
]
