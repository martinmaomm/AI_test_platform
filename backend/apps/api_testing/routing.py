"""
WebSocket路由配置
支持动态路由注册和消费者类型管理
"""

from django.urls import re_path
from common.websocket import (
    WebSocketConsumerFactory,
    StreamingConsumer, 
    TaskStatusConsumer
)

# 定义WebSocket路由模式
WEBSOCKET_URL_PATTERNS = [
    # 场景生成流式输出WebSocket
    re_path(r'ws/scenario_generation-streaming/$', StreamingConsumer.as_asgi()),
    
    # MidScene脚本生成流式输出WebSocket
    re_path(r'ws/midscene_script_generation-streaming/$', StreamingConsumer.as_asgi()),
    
    # WebUI测试流式输出WebSocket
    re_path(r'ws/webui_test_generation-streaming/$', StreamingConsumer.as_asgi()),
    
    # WebUI自动化测试流式输出WebSocket
    re_path(r'ws/webui_auto_test-streaming/$', StreamingConsumer.as_asgi()),
    
    # 任务状态更新WebSocket
    re_path(r'ws/task-status/(?P<task_id>[^/]+)/$', TaskStatusConsumer.as_asgi()),
]

# 导出路由模式
websocket_urlpatterns = WEBSOCKET_URL_PATTERNS

# 动态路由注册函数
def register_websocket_route(pattern: str, consumer_class, name: str = None):
    """
    动态注册WebSocket路由
    
    Args:
        pattern: URL模式
        consumer_class: 消费者类
        name: 路由名称（可选）
    """
    global WEBSOCKET_URL_PATTERNS
    route = re_path(pattern, consumer_class.as_asgi())
    if name:
        route.name = name
    WEBSOCKET_URL_PATTERNS.append(route)

def get_websocket_routes():
    """获取所有WebSocket路由"""
    return WEBSOCKET_URL_PATTERNS

def get_consumer_types():
    """获取所有支持的消费者类型"""
    return WebSocketConsumerFactory.get_all_consumer_types()