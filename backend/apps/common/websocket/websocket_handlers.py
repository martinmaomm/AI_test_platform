"""
WebSocket处理器模块
基于统一核心模块的WebSocket消费者实现
"""

import json
import logging
from typing import Dict, Any

from .websocket_core import (
    BaseWebSocketConsumer, 
    StreamingConsumerMixin, 
    TaskStatusMixin,
    WebSocketConfig,
    AnonymousUser
)

logger = logging.getLogger(__name__)


class StreamingConsumer(BaseWebSocketConsumer, StreamingConsumerMixin, TaskStatusMixin):
    """通用流式输出WebSocket消费者"""
    
    def get_room_type(self) -> str:
        """从URL路由中获取房间类型"""
        # 从URL路径中提取room_type，例如: /ws/scenario_generation-streaming/
        url_path = self.scope.get('path', '')
        if '-streaming' in url_path:
            # 提取路径的最后一部分，去掉-streaming后缀
            path_parts = url_path.split('/')
            last_part = path_parts[-1] if path_parts[-1] else path_parts[-2]
            room_type = last_part.replace('-streaming', '')
            return room_type
        return "scenario"  # 默认值
    
    async def receive(self, text_data):
        """接收WebSocket消息"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == WebSocketConfig.MESSAGE_TYPES['PING']:
                await self.send(text_data=json.dumps({'type': WebSocketConfig.MESSAGE_TYPES['PONG']}))
            else:
                logger.warning(f"未知的消息类型: {message_type}")
                
        except json.JSONDecodeError:
            logger.error("无效的JSON消息")
            await self.send_error('无效的JSON消息')
        except Exception as e:
            logger.error(f"处理WebSocket消息时发生错误: {e}")
            await self.send_error(f'处理消息时发生错误: {str(e)}')


class TaskStatusConsumer(BaseWebSocketConsumer, TaskStatusMixin):
    """任务状态更新WebSocket消费者"""
    
    def get_room_type(self) -> str:
        return "task_status"
    
    async def connect(self):
        """建立WebSocket连接（任务状态专用）"""
        try:
            # 初始化room_type和配置
            self.room_type = self.get_room_type()
            self.consumer_config = WebSocketConfig.get_consumer_config(self.room_type)
            
            # 获取任务ID
            self.task_id = self.scope['url_route']['kwargs']['task_id']
            if not self.task_id:
                await self.close(code=4000)
                return
            
            # 获取用户信息
            self.user = self.scope.get('user', AnonymousUser())
            if isinstance(self.user, AnonymousUser):
                await self.close(code=4001)
                return
            
            # 生成房间名称
            self.room_name = f"task_status_{self.task_id}"
            self.room_group_name = self.room_name
            
            # 加入房间组
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            
            # 接受连接
            await self.accept()
            
            # 发送连接成功消息
            await self.send_connection_success()
            
            logger.info(f"用户 {self.user.username} 连接任务状态WebSocket成功，任务ID: {self.task_id}")
            
        except Exception as e:
            logger.error(f"任务状态WebSocket连接失败: {e}")
            await self.close(code=4000)
    
    async def receive(self, text_data):
        """接收WebSocket消息"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == WebSocketConfig.MESSAGE_TYPES['PING']:
                await self.send(text_data=json.dumps({'type': WebSocketConfig.MESSAGE_TYPES['PONG']}))
            else:
                logger.warning(f"任务状态消费者收到未知消息类型: {message_type}")
                
        except json.JSONDecodeError:
            logger.error("任务状态消费者收到无效的JSON消息")
            await self.send_error('无效的JSON消息')
        except Exception as e:
            logger.error(f"任务状态消费者处理消息时发生错误: {e}")
            await self.send_error(f'处理消息时发生错误: {str(e)}')


# ============ 消费者工厂 ============

class WebSocketConsumerFactory:
    """WebSocket消费者工厂类"""
    
    _consumers = {
        'scenario_generation': StreamingConsumer,
        'midscene_script_generation': StreamingConsumer,
        'webui_test_generation': StreamingConsumer,
        'webui_auto_test': StreamingConsumer,
        'task_status': TaskStatusConsumer,
    }
    
    @classmethod
    def get_consumer_class(cls, consumer_type: str):
        """获取消费者类"""
        return cls._consumers.get(consumer_type)
    
    @classmethod
    def get_all_consumer_types(cls):
        """获取所有支持的消费者类型"""
        return list(cls._consumers.keys())
    
    @classmethod
    def register_consumer(cls, consumer_type: str, consumer_class):
        """注册新的消费者类型"""
        cls._consumers[consumer_type] = consumer_class
    
    @classmethod
    def create_consumer(cls, consumer_type: str, *args, **kwargs):
        """创建消费者实例"""
        consumer_class = cls.get_consumer_class(consumer_type)
        if not consumer_class:
            raise ValueError(f"不支持的消费者类型: {consumer_type}")
        return consumer_class(*args, **kwargs)
