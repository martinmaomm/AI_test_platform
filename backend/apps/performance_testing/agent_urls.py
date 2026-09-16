from django.urls import path

from .agent_views import AgentEnrollView, AgentHeartbeatView
from .installation_views import InstallScriptView


app_name = 'performance_agent'

urlpatterns = [
    path('install/install.sh', InstallScriptView.as_view(), name='install-script'),
    path('enroll/', AgentEnrollView.as_view(), name='enroll'),
    path('heartbeat/', AgentHeartbeatView.as_view(), name='heartbeat'),
]
