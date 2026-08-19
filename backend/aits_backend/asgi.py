"""
ASGI config for aits_backend project.
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from common.websocket import WebSocketJWTAuthMiddlewareStack
from api_testing import routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aits_backend.settings')

# 获取Django ASGI应用
django_asgi_app = get_asgi_application()

# 定义ASGI应用
application = ProtocolTypeRouter({
    # HTTP请求路由到Django
    "http": django_asgi_app,
    
    # WebSocket请求路由到Channels，使用JWT认证中间件
    "websocket": WebSocketJWTAuthMiddlewareStack(
        URLRouter(
            routing.websocket_urlpatterns
        )
    ),
})
