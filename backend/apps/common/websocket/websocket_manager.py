"""
WebSocket服务管理器模块
简化的WebSocket服务管理，基于统一核心模块
"""

import logging
from typing import Dict, Any, Optional

from .websocket_core import (
    WebSocketMessageService, 
    WebSocketConfig,
    websocket_message_service
)

logger = logging.getLogger(__name__)


class WebSocketServiceManager:
    """WebSocket服务管理器"""
    
    def __init__(self):
        self.service = websocket_message_service
    
    def get_connection_info(self) -> Dict[str, Any]:
        """获取连接信息"""
        return {
            'status': 'active' if self.service.is_available() else 'inactive',
            'supported_consumers': WebSocketConfig.get_all_consumer_types(),
            'consumer_configs': {
                consumer_type: WebSocketConfig.get_consumer_config(consumer_type)
                for consumer_type in WebSocketConfig.get_all_consumer_types()
            }
        }
    
    def send_streaming_output(self, user_id: int, step: str, content: str, 
                            task_id: Optional[str] = None, timestamp: Optional[str] = None, 
                            room_type: str = "scenario") -> bool:
        """发送流式输出消息"""
        return self.service.send_streaming_output(user_id, step, content, task_id, timestamp, room_type)
    
    def send_node_start_notification(self, user_id: int, node_name: str, 
                                   node_display_name: str, task_id: Optional[str] = None, 
                                   timestamp: Optional[str] = None, room_type: str = "scenario") -> bool:
        """发送节点开始执行通知"""
        return self.service.send_node_start_notification(user_id, node_name, node_display_name, task_id, timestamp, room_type)
    
    def send_task_status_update(self, task_id: str, status: str, progress: int = 0, 
                              message: str = '', timestamp: Optional[str] = None) -> bool:
        """发送任务状态更新"""
        return self.service.send_task_status_update(task_id, status, progress, message, timestamp)
    
    def send_task_completed(self, user_id: int, task_id: str, result: Dict[str, Any], 
                          message: str = '任务完成', timestamp: Optional[str] = None, 
                          room_type: str = "scenario") -> bool:
        """发送任务完成消息"""
        return self.service.send_task_completed(user_id, task_id, result, message, timestamp, room_type)
    
    def send_task_failed(self, user_id: int, task_id: str, error: str, 
                       message: str = '任务执行失败', timestamp: Optional[str] = None, 
                       room_type: str = "scenario") -> bool:
        """发送任务失败消息"""
        return self.service.send_task_failed(user_id, task_id, error, message, timestamp, room_type)
    
    def get_service_statistics(self) -> Dict[str, Any]:
        """获取服务统计信息"""
        return {
            'total_consumer_types': len(WebSocketConfig.get_all_consumer_types()),
            'consumer_types': WebSocketConfig.get_all_consumer_types(),
            'service_status': 'active' if self.service.is_available() else 'inactive'
        }


# 创建全局WebSocket服务管理器实例
websocket_service_manager = WebSocketServiceManager()
