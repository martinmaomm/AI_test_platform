from django.urls import path
from . import views

urlpatterns = [
    path('plans/', views.PlanListView.as_view()),
    path('plans/<uuid:plan_id>/', views.PlanDetailView.as_view()),
    path('plans/<uuid:plan_id>/generate/', views.PlanGenerateView.as_view()),
    path('plans/<uuid:plan_id>/runs/', views.PlanRunView.as_view()),
    path('tasks/', views.TaskListView.as_view()),
    path('tasks/<uuid:task_id>/', views.TaskDetailView.as_view()),
    path('tasks/<uuid:task_id>/stop/', views.TaskStopView.as_view()),
    path('tasks/<uuid:task_id>/report.html', views.TaskReportView.as_view()),
    path('sources/', views.SourcesView.as_view()),
]
