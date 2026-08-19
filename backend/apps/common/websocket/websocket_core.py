"""
WebSocket核心模块
统一的WebSocket服务核心，提供认证、消息发送、连接管理等功能
"""

import json
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from urllib.parse import parse_qs

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.middleware import BaseMiddleware
from channels.layers import get_channel_layer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)
User = get_user_model()


# ============ 配置和常量 ============

class WebSocketConfig:
    """WebSocket配置类"""
    
    # 支持的消费者类型配置
    CONSUMER_TYPES = {
        'scenario_generation': {
            'room_type': 'scenario_generation',
            'display_name': '场景生成',
            'description': 'API测试场景生成流式输出'
        },
        'midscene_script_generation': {
            'room_type': 'midscene_script_generation', 
            'display_name': 'MidScene脚本生成',
            'description': 'MidScene测试脚本生成流式输出'
        },
        'webui_test_generation': {
            'room_type': 'webui_test_generation',
            'display_name': 'WebUI测试生成',
            'description': 'WebUI测试用例生成流式输出'
        },
        'webui_auto_test': {
            'room_type': 'webui_auto_test',
            'display_name': 'WebUI自动化测试',
            'description': 'WebUI自动化测试流式输出'
        },
        'task_status': {
            'room_type': 'task_status',
            'display_name': '任务状态',
            'description': '任务状态更新通知'
        }
    }
    
    # 消息类型常量
    MESSAGE_TYPES = {
        'CONNECTION_SUCCESS': 'connection_success',
        'STREAMING_OUTPUT': 'streaming_output',
        'STREAMING_COMPLETE': 'streaming_complete',
        'NODE_START': 'node_start',
        'TASK_STATUS_UPDATE': 'task_status_update',
        'TASK_COMPLETED': 'task_completed',
        'TASK_FAILED': 'task_failed',
        'GENERATION_STARTED': 'generation_started',
        'TASK_STARTED': 'task_started',
        'ERROR': 'error',
        'PING': 'ping',
        'PONG': 'pong'
    }
    
    @classmethod
    def get_consumer_config(cls, consumer_type: str) -> Dict[str, Any]:
        """获取消费者配置"""
        return cls.CONSUMER_TYPES.get(consumer_type, {})
    
    @classmethod
    def get_all_consumer_types(cls) -> List[str]:
        """获取所有支持的消费者类型"""
        return list(cls.CONSUMER_TYPES.keys())


# ============ 认证中间件 ============

class WebSocketJWTAuthMiddleware(BaseMiddleware):
    """WebSocket JWT认证中间件"""
    
    def __init__(self, inner):
        super().__init__(inner)
    
    async def __call__(self, scope, receive, send):
        user = await self.get_user_from_scope(scope)
        scope['user'] = user
        return await super().__call__(scope, receive, send)
    
    async def get_user_from_scope(self, scope):
        """从WebSocket scope中获取用户信息"""
        try:
            # 尝试从查询参数获取token
            token = self._get_token_from_query_string(scope)
            
            # 如果查询参数中没有，尝试从headers获取
            if not token:
                token = self._get_token_from_headers(scope)
            
            if not token:
                logger.warning("WebSocket连接缺少JWT token")
                return AnonymousUser()
            
            # 验证token并获取用户
            user = await self._get_user_from_token(token)
            if user:
                logger.info(f"WebSocket用户认证成功: {user.username}")
                return user
            else:
                logger.warning("WebSocket JWT token验证失败")
                return AnonymousUser()
                
        except Exception as e:
            logger.error(f"WebSocket认证过程中发生错误: {e}")
            return AnonymousUser()
    
    def _get_token_from_query_string(self, scope):
        """从查询参数中获取JWT token"""
        try:
            query_string = scope.get('query_string', b'').decode('utf-8')
            query_params = parse_qs(query_string)
            
            # 尝试从不同参数名获取token
            for param_name in ['token', 'access_token']:
                token_list = query_params.get(param_name, [])
                if token_list:
                    return token_list[0]
                    
        except Exception as e:
            logger.error(f"从查询参数获取token失败: {e}")
        
        return None
    
    def _get_token_from_headers(self, scope):
        """从headers中获取JWT token"""
        try:
            headers = dict(scope.get('headers', []))
            
            # 尝试从Authorization header获取
            auth_header = headers.get(b'authorization', b'').decode('utf-8')
            if auth_header.startswith('Bearer '):
                return auth_header[7:]  # 移除 'Bearer ' 前缀
            
            # 尝试从自定义header获取
            token_header = headers.get(b'x-access-token', b'').decode('utf-8')
            if token_header:
                return token_header
                
        except Exception as e:
            logger.error(f"从headers获取token失败: {e}")
        
        return None
    
    @database_sync_to_async
    def _get_user_from_token(self, token):
        """从JWT token中获取用户信息"""
        try:
            access_token = AccessToken(token)
            user_id = access_token.get('user_id')
            
            if not user_id:
                logger.warning("JWT token中缺少user_id")
                return None
            
            try:
                user = User.objects.get(id=user_id)
                return user
            except User.DoesNotExist:
                logger.warning(f"用户不存在: {user_id}")
                return None
                
        except (InvalidToken, TokenError) as e:
            logger.warning(f"JWT token验证失败: {e}")
            return None
        except Exception as e:
            logger.error(f"获取用户信息时发生错误: {e}")
            return None


def WebSocketJWTAuthMiddlewareStack(inner):
    """WebSocket JWT认证中间件栈"""
    return WebSocketJWTAuthMiddleware(inner)


# ============ 基础消费者类 ============

class BaseWebSocketConsumer(AsyncWebsocketConsumer, ABC):
    """WebSocket消费者基类"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.room_group_name = None
        self.room_name = None
        self.room_type = None
        self.consumer_config = None
    
    @abstractmethod
    def get_room_type(self) -> str:
        """获取房间类型，子类必须实现"""
        pass
    
    async def connect(self):
        """建立WebSocket连接"""
        try:
            # 初始化room_type和配置
            self.room_type = self.get_room_type()
            self.consumer_config = WebSocketConfig.get_consumer_config(self.room_type)
            
            self.user = self.scope.get('user', AnonymousUser())
            
            if isinstance(self.user, AnonymousUser):
                await self.close(code=4001)  # 未认证
                return
            
            # 生成房间名称
            self.room_name = f"{self.room_type}_streaming_{self.user.id}"
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
            
            logger.info(f"用户 {self.user.username} 连接{self.consumer_config.get('display_name', 'WebSocket')}成功")
            
        except Exception as e:
            logger.error(f"WebSocket连接失败: {e}")
            await self.close(code=4000)  # 连接错误
    
    async def disconnect(self, close_code):
        """断开WebSocket连接"""
        try:
            if self.room_group_name:
                await self.channel_layer.group_discard(
                    self.room_group_name,
                    self.channel_name
                )
            
            logger.info(f"用户 {self.user.username if self.user else 'Unknown'} 断开{self.consumer_config.get('display_name', 'WebSocket')}连接，代码: {close_code}")
            
        except Exception as e:
            logger.error(f"断开WebSocket连接时发生错误: {e}")
    
    async def send_connection_success(self):
        """发送连接成功消息"""
        await self.send(text_data=json.dumps({
            'type': WebSocketConfig.MESSAGE_TYPES['CONNECTION_SUCCESS'],
            'message': f'{self.consumer_config.get("display_name", "WebSocket")}连接成功',
            'consumer_type': self.room_type,
            'timestamp': self.get_timestamp()
        }))
    
    async def send_error(self, message: str, error_type: str = 'error'):
        """发送错误消息"""
        await self.send(text_data=json.dumps({
            'type': error_type,
            'message': message,
            'timestamp': self.get_timestamp()
        }))
    
    def get_timestamp(self) -> str:
        """获取当前时间戳"""
        return datetime.now().isoformat()
    
    async def check_project_permission(self, project_id: int) -> bool:
        """检查用户是否有权限访问项目"""
        try:
            from projects.models import Project
            project = await database_sync_to_async(Project.objects.get)(id=project_id)
            
            # 检查用户是否是项目所有者或成员
            is_owner = project.owner == self.user
            is_member = await database_sync_to_async(
                project.members.filter(user=self.user).exists
            )()
            
            return is_owner or is_member
            
        except ObjectDoesNotExist:
            return False
        except Exception as e:
            logger.error(f"检查项目权限时发生错误: {e}")
            return False


# ============ 混入类 ============

class StreamingConsumerMixin:
    """流式输出消费者混入类"""
    
    async def streaming_output(self, event):
        """发送流式输出消息"""
        try:
            await self.send(text_data=json.dumps({
                'type': WebSocketConfig.MESSAGE_TYPES['STREAMING_OUTPUT'],
                'step': event['step'],
                'content': event['content'],
                'timestamp': event.get('timestamp'),
                'task_id': event.get('task_id')
            }))
        except Exception as e:
            logger.error(f"发送流式输出消息时发生错误: {e}")
    
    async def streaming_complete(self, event):
        """发送流式输出完成消息"""
        try:
            await self.send(text_data=json.dumps({
                'type': WebSocketConfig.MESSAGE_TYPES['STREAMING_COMPLETE'],
                'step': event['step'],
                'timestamp': event.get('timestamp')
            }))
        except Exception as e:
            logger.error(f"发送流式输出完成消息时发生错误: {e}")
    
    async def node_start(self, event):
        """发送节点开始执行通知"""
        try:
            await self.send(text_data=json.dumps({
                'type': WebSocketConfig.MESSAGE_TYPES['NODE_START'],
                'node_name': event['node_name'],
                'node_display_name': event['node_display_name'],
                'node_description': event.get('node_description', ''),
                'task_id': event.get('task_id'),
                'timestamp': event.get('timestamp')
            }))
        except Exception as e:
            logger.error(f"发送节点开始通知时发生错误: {e}")


class TaskStatusMixin:
    """任务状态消费者混入类"""
    
    async def task_status_update(self, event):
        """发送任务状态更新消息"""
        try:
            await self.send(text_data=json.dumps({
                'type': WebSocketConfig.MESSAGE_TYPES['TASK_STATUS_UPDATE'],
                'task_id': event['task_id'],
                'status': event['status'],
                'progress': event.get('progress', 0),
                'message': event.get('message', ''),
                'timestamp': event.get('timestamp')
            }))
        except Exception as e:
            logger.error(f"发送任务状态更新消息时发生错误: {e}")
    
    async def task_completed(self, event):
        """发送任务完成消息"""
        try:
            await self.send(text_data=json.dumps({
                'type': WebSocketConfig.MESSAGE_TYPES['TASK_COMPLETED'],
                'task_id': event['task_id'],
                'result': event.get('result'),
                'message': event.get('message', '任务完成'),
                'timestamp': event.get('timestamp')
            }))
        except Exception as e:
            logger.error(f"发送任务完成消息时发生错误: {e}")
    
    async def task_failed(self, event):
        """发送任务失败消息"""
        try:
            await self.send(text_data=json.dumps({
                'type': WebSocketConfig.MESSAGE_TYPES['TASK_FAILED'],
                'task_id': event['task_id'],
                'error': event.get('error'),
                'message': event.get('message', '任务失败'),
                'timestamp': event.get('timestamp')
            }))
        except Exception as e:
            logger.error(f"发送任务失败消息时发生错误: {e}")


# ============ 消息发送服务 ============

class WebSocketMessageService:
    """WebSocket消息发送服务"""
    
    def __init__(self):
        self.channel_layer = None
        self._ensure_channel_layer()
    
    def _ensure_channel_layer(self):
        """确保Channels层可用"""
        try:
            self.channel_layer = get_channel_layer()
            if self.channel_layer:
                logger.debug("Channels层初始化成功")
            else:
                logger.warning("Channels层未初始化")
        except Exception as e:
            logger.error(f"初始化Channels层失败: {e}")
            self.channel_layer = None
    
    def _send_message(self, room_group_name: str, message_data: Dict[str, Any], log_message: str = "") -> bool:
        """通用消息发送方法"""
        if not self.channel_layer:
            logger.error("Channels层未设置，无法发送WebSocket消息")
            return False
        
        try:
            self._send_async_message(room_group_name, message_data)
            return True
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False
    
    def _send_async_message(self, room_group_name: str, message_data: Dict[str, Any]):
        """异步发送消息"""
        try:
            # 检查是否已有事件循环
            try:
                loop = asyncio.get_running_loop()
                asyncio.create_task(self._send_message_async(room_group_name, message_data))
            except RuntimeError:
                # 如果没有事件循环，创建新的
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._send_message_async(room_group_name, message_data))
                finally:
                    loop.close()
        except Exception as e:
            logger.error(f"异步发送消息失败: {e}")
    
    async def _send_message_async(self, room_group_name: str, message_data: Dict[str, Any]):
        """真正的异步发送消息方法"""
        try:
            await self.channel_layer.group_send(room_group_name, message_data)
            logger.debug(f"异步消息发送成功: {room_group_name}")
        except Exception as e:
            logger.error(f"异步消息发送失败: {e}")
    
    def send_streaming_output(self, user_id: int, step: str, content: str, 
                            task_id: Optional[str] = None, timestamp: Optional[str] = None, 
                            room_type: str = "scenario") -> bool:
        """发送流式输出消息"""
        message_data = {
            'type': WebSocketConfig.MESSAGE_TYPES['STREAMING_OUTPUT'],
            'step': step,
            'content': content,
            'task_id': task_id,
            'timestamp': timestamp
        }
        
        room_group_name = f"{room_type}_streaming_{user_id}"
        return self._send_message(room_group_name, message_data, f"流式输出到用户 {user_id}: {step}")
    
    def send_task_status_update(self, task_id: str, status: str, progress: int = 0, 
                              message: str = '', timestamp: Optional[str] = None) -> bool:
        """发送任务状态更新消息"""
        message_data = {
            'type': WebSocketConfig.MESSAGE_TYPES['TASK_STATUS_UPDATE'],
            'task_id': task_id,
            'status': status,
            'progress': progress,
            'message': message,
            'timestamp': timestamp
        }
        
        room_group_name = f"task_status_{task_id}"
        return self._send_message(room_group_name, message_data, f"任务状态更新: {task_id} - {status}")
    
    def send_task_completed(self, user_id: int, task_id: str, result: Dict[str, Any], 
                          message: str = '任务完成', timestamp: Optional[str] = None, 
                          room_type: str = "scenario") -> bool:
        """发送任务完成消息"""
        message_data = {
            'type': WebSocketConfig.MESSAGE_TYPES['TASK_COMPLETED'],
            'task_id': task_id,
            'result': result,
            'message': message,
            'timestamp': timestamp
        }
        
        room_group_name = f"{room_type}_streaming_{user_id}"
        return self._send_message(room_group_name, message_data, f"任务完成消息: 用户{user_id}, 任务{task_id}")
    
    def send_task_failed(self, user_id: int, task_id: str, error: str, 
                       message: str = '任务执行失败', timestamp: Optional[str] = None, 
                       room_type: str = "scenario") -> bool:
        """发送任务失败消息"""
        message_data = {
            'type': WebSocketConfig.MESSAGE_TYPES['TASK_FAILED'],
            'task_id': task_id,
            'error': error,
            'message': message,
            'timestamp': timestamp
        }
        
        room_group_name = f"{room_type}_streaming_{user_id}"
        return self._send_message(room_group_name, message_data, f"任务失败消息: 用户{user_id}, 任务{task_id}")
    
    def send_node_start_notification(self, user_id: int, node_name: str, node_display_name: str, 
                                   node_description: Optional[str] = None, task_id: Optional[str] = None, 
                                   timestamp: Optional[str] = None, room_type: str = "scenario") -> bool:
        """发送节点开始执行通知"""
        message_data = {
            'type': WebSocketConfig.MESSAGE_TYPES['NODE_START'],
            'node_name': node_name,
            'node_display_name': node_display_name,
            'node_description': node_description or '',
            'task_id': task_id,
            'timestamp': timestamp
        }
        
        room_group_name = f"{room_type}_streaming_{user_id}"
        return self._send_message(room_group_name, message_data, f"节点开始通知: 用户{user_id}, 节点{node_name}")
    
    def is_available(self) -> bool:
        """检查WebSocket服务是否可用"""
        return self.channel_layer is not None


# ============ 辅助函数 ============

def send_node_start_notification_helper(
    user_id: Optional[int],
    node_name: str,
    node_display_name: str,
    node_description: Optional[str] = None,
    enable_streaming: bool = True,
    room_type: str = "default",
    task_id: Optional[str] = None
) -> bool:
    """
    发送节点开始执行通知的辅助函数
    
    Args:
        user_id: 用户ID（可选）
        node_name: 节点名称
        node_display_name: 节点显示名称
        node_description: 节点描述（可选）
        enable_streaming: 是否启用流式输出
        room_type: WebSocket房间类型
        task_id: 任务ID（可选）
        
    Returns:
        bool: 是否发送成功
    """
    if not enable_streaming or not user_id:
        return False
    
    try:
        if not websocket_message_service.is_available():
            logger.warning("WebSocket服务不可用，无法发送节点通知")
            return False
        
        timestamp = datetime.now().isoformat()
        success = websocket_message_service.send_node_start_notification(
            user_id=user_id,
            node_name=node_name,
            node_display_name=node_display_name,
            node_description=node_description,
            task_id=task_id,
            timestamp=timestamp,
            room_type=room_type
        )
        
        if success:
            logger.debug(f"节点开始通知发送成功: [{node_name}] {node_display_name}")
        else:
            logger.warning(f"节点开始通知发送失败: [{node_name}]")
        
        return success
    except Exception as e:
        logger.warning(f"发送节点开始通知失败: {e}")
        return False


# 创建全局WebSocket消息服务实例
websocket_message_service = WebSocketMessageService()
