from django.urls import path

from .agent_views import AgentEnrollView, AgentHeartbeatView
from .installation_views import InstallCACertificateView, InstallScriptView, UpgradeScriptView


app_name = 'performance_agent'

urlpatterns = [
    path('install/install.sh', InstallScriptView.as_view(), name='install-script'),
    path('install/upgrade-node.py', UpgradeScriptView.as_view(), name='upgrade-script'),
    path('install/ca.pem', InstallCACertificateView.as_view(), name='install-ca'),
    path('enroll/', AgentEnrollView.as_view(), name='enroll'),
    path('heartbeat/', AgentHeartbeatView.as_view(), name='heartbeat'),
]
