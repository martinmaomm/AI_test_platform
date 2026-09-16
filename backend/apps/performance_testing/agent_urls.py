from django.urls import path

from .agent_views import AgentEnrollView, AgentHeartbeatView


app_name = 'performance_agent'

urlpatterns = [
    path('enroll/', AgentEnrollView.as_view(), name='enroll'),
    path('heartbeat/', AgentHeartbeatView.as_view(), name='heartbeat'),
]
