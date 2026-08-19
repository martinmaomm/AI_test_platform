"""
Celery任务通用工具函数
提供任务执行、进度更新、WebSocket通信等通用功能
"""
import logging
from typing import Dict, Any, Optional, Callable
from celery import shared_task

logger = logging.getLogger(__name__)


def execute_async_task_with_progress(task_instance, task_name: str, 
                                     task_executor: Callable,
                                     *args, **kwargs) -> Dict[str, Any]:
    """
    通用异步任务执行框架，支持进度更新和错误处理
    
    适用于不需要WebSocket实时通信的任务，如：
    - 端点测试用例生成
    - 批量数据处理
    - 后台计算任务
    
    Args:
        task_instance: Celery任务实例
        task_name: 任务名称（用于日志）
        task_executor: 实际执行任务的函数
        *args, **kwargs: 传递给task_executor的参数
    
    Returns:
        任务执行结果
    """
    task_id = task_instance.request.id
    
    try:
        # 初始化进度
        update_task_progress(task_instance, 0, f'正在初始化{task_name}...')
        
        # 执行实际任务（进度更新由具体执行函数负责）
        result = task_executor(task_instance, *args, **kwargs)
        
        # 通过status='completed'判断任务是否执行完成，而不是success字段
        if result and result.get('status') == 'completed':
            # 任务执行完成（无论成功或失败），更新最终进度
            update_task_progress(task_instance, 100, f'{task_name}完成')
            
            # 更新任务状态为完成
            update_task_success(task_instance, f'{task_name}完成', result)
            
            logger.info(f"{task_name}任务完成: {task_id}")
            return result
        else:
            # 任务未完成或执行异常，更新任务状态为失败
            error_msg = result.get('error', '未知错误') if result else '任务执行失败'
            logger.error(f"{task_name}失败: {error_msg}")
            
            # 更新任务状态为失败，确保前端能够获得错误信息
            task_instance.update_state(
                state='FAILURE',
                meta={
                    'status': 'failed',
                    'error': error_msg,
                    'task_id': task_id,
                    'result': result,
                    'exc_type': 'TaskExecutionError',
                    'exc_message': error_msg,
                    'exc_module': 'util.celery_task_utils'
                }
            )
            
            return result
            
    except Exception as e:
        error_msg = f'{task_name}任务异常: {str(e)}'
        logger.error(error_msg, exc_info=True)
        
        # 构建统一的错误结果
        error_result = build_error_result(task_id, error_msg)
        
        # 更新任务状态为失败
        task_instance.update_state(
            state='FAILURE',
            meta={
                'status': 'failed',
                'error': str(e),
                'task_id': task_id,
                'exc_type': type(e).__name__,
                'exc_message': str(e),
                'exc_module': type(e).__module__
            }
        )
        
        return error_result


def execute_async_task_with_websocket(task_instance, task_name: str, 
                                      task_executor: Callable,
                                      *args, **kwargs) -> Dict[str, Any]:
    """
    支持WebSocket的异步任务执行框架，支持进度更新、错误处理和WebSocket消息发送
    
    适用于需要实时WebSocket通信的任务，如：
    - 智能场景生成（需要流式输出）
    - 实时数据处理
    - 需要用户交互的任务
    
    Args:
        task_instance: Celery任务实例
        task_name: 任务名称（用于日志）
        task_executor: 实际执行任务的函数
        *args, **kwargs: 传递给task_executor的参数
    
    Returns:
        任务执行结果
    """
    task_id = task_instance.request.id
    
    try:
        # 初始化进度
        update_task_progress(task_instance, 0, f'正在初始化{task_name}...')
        
        # 执行实际任务（进度更新由具体执行函数负责）
        result = task_executor(task_instance, *args, **kwargs)
        
        # 通过status='completed'判断任务是否执行完成，而不是success字段
        if result and result.get('status') == 'completed':
            # 任务执行完成（无论成功或失败），更新最终进度
            update_task_progress(task_instance, 100, f'{task_name}完成')
            
            # 更新任务状态为完成
            update_task_success(task_instance, f'{task_name}完成', result)
            
            # 发送WebSocket任务完成消息
            # 直接使用任务名称作为房间类型
            _send_websocket_task_completed(task_instance, result, task_name.lower())
            
            logger.info(f"{task_name}任务完成: {task_id}")
            return result
        else:
            # 任务未完成或执行异常，更新任务状态为失败
            error_msg = result.get('error', '未知错误') if result else '任务执行失败'
            logger.error(f"{task_name}失败: {error_msg}")
            
            # 更新任务状态为失败，确保前端能够获得错误信息
            task_instance.update_state(
                state='FAILURE',
                meta={
                    'status': 'failed',
                    'error': error_msg,
                    'task_id': task_id,
                    'result': result,
                    'exc_type': 'TaskExecutionError',
                    'exc_message': error_msg,
                    'exc_module': 'util.celery_task_utils'
                }
            )
            
            # 发送WebSocket任务失败消息
            # 直接使用任务名称作为房间类型
            _send_websocket_task_failed(task_instance, error_msg, task_name.lower())
            
            return result
            
    except Exception as e:
        error_msg = f'{task_name}任务异常: {str(e)}'
        logger.error(error_msg, exc_info=True)
        
        # 构建统一的错误结果
        error_result = build_error_result(task_id, error_msg)
        
        # 更新任务状态为失败
        task_instance.update_state(
            state='FAILURE',
            meta={
                'status': 'failed',
                'error': str(e),
                'task_id': task_id,
                'exc_type': type(e).__name__,
                'exc_message': str(e),
                'exc_module': type(e).__module__
            }
        )
        
        # 发送WebSocket任务失败消息
        # 直接使用任务名称作为房间类型
        _send_websocket_task_failed(task_instance, str(e), task_name.lower())
        
        return error_result


def update_task_progress(task_instance, progress: int, message: str):
    """更新任务进度"""
    task_instance.update_state(
        state='PROGRESS',
        meta={
            'status': 'processing',
            'message': message,
            'progress': progress
        }
    )


def update_task_success(task_instance, message: str, result: Dict[str, Any]):
    """更新任务状态为成功"""
    task_instance.update_state(
        state='SUCCESS',
        meta={
            'status': 'completed',
            'message': message,
            'progress': 100,
            'result': result
        }
    )
    return result


def build_error_result(task_id: Optional[str], error_message: str) -> Dict[str, Any]:
    """构建统一的错误结果"""
    return {
        'success': False,
        'status': 'failed',
        'error': error_message,
        'message': error_message,  # 统一message字段
        'task_id': task_id
    }


def _extract_user_id_from_task(task_instance) -> Optional[int]:
    """从任务实例中提取用户ID"""
    try:
        # 尝试从任务参数中获取user_id
        if hasattr(task_instance.request, 'kwargs') and 'user_id' in task_instance.request.kwargs:
            return task_instance.request.kwargs['user_id']
        elif hasattr(task_instance.request, 'args') and len(task_instance.request.args) > 2:
            # 对于不同的任务，user_id的位置不同
            # 场景生成任务：user_id通常是第3个参数（索引2）
            # WebUI测试脚本生成任务：user_id是第4个参数（索引3）
            args = task_instance.request.args
            if len(args) >= 4:
                # 检查第4个参数是否是整数（user_id）
                if isinstance(args[3], int):
                    return args[3]
            if len(args) >= 3:
                # 检查第3个参数是否是整数（user_id）
                if isinstance(args[2], int):
                    return args[2]
        return None
    except Exception as e:
        logger.warning(f"提取用户ID失败: {e}")
        return None


def _send_websocket_task_completed(task_instance, result: Dict[str, Any], room_type: str = "scenario"):
    """发送WebSocket任务完成消息（内部方法）"""
    try:
        from ..websocket.websocket_core import websocket_message_service
        
        # 获取用户ID和任务ID
        task_id = task_instance.request.id
        user_id = _extract_user_id_from_task(task_instance)
        
        if user_id:
            # 直接调用websocket_service中的方法
            success = websocket_message_service.send_task_completed(
                user_id=user_id,
                task_id=task_id,
                result=result,
                message='任务完成',
                timestamp=None,
                room_type=room_type
            )
            
            if success:
                logger.info(f"已发送WebSocket任务完成消息: 用户{user_id}, 任务{task_id}")
            else:
                logger.warning(f"WebSocket任务完成消息发送失败: 用户{user_id}, 任务{task_id}")
        else:
            logger.warning(f"无法获取用户ID，跳过WebSocket任务完成消息发送: {task_id}")
            
    except Exception as e:
        logger.error(f"发送WebSocket任务完成消息失败: {e}")


def _send_websocket_task_failed(task_instance, error_message: str, room_type: str = "scenario"):
    """发送WebSocket任务失败消息（内部方法）"""
    try:
        from ..websocket.websocket_core import websocket_message_service
        
        # 获取用户ID和任务ID
        task_id = task_instance.request.id
        user_id = _extract_user_id_from_task(task_instance)
        
        if user_id:
            # 直接调用websocket_service中的方法
            success = websocket_message_service.send_task_failed(
                user_id=user_id,
                task_id=task_id,
                error=error_message,
                message='任务执行失败',
                timestamp=None,
                room_type=room_type
            )
            
            if success:
                logger.info(f"已发送WebSocket任务失败消息: 用户{user_id}, 任务{task_id}")
            else:
                logger.warning(f"WebSocket任务失败消息发送失败: 用户{user_id}, 任务{task_id}")
        else:
            logger.warning(f"无法获取用户ID，跳过WebSocket任务失败消息发送: {task_id}")
            
    except Exception as e:
        logger.error(f"发送WebSocket任务失败消息失败: {e}")


def get_celery_task_status(task_id: str) -> Dict[str, Any]:
    """
    获取AI任务状态
    
    Args:
        task_id: Celery任务ID
    
    Returns:
        任务状态信息
    """
    try:
        from celery.result import AsyncResult
        
        result = AsyncResult(task_id)
        
        # 检查任务状态
        task_state = result.state
        
        # 如果任务状态为PENDING且没有info，可能是任务不存在
        if task_state == 'PENDING':
            info = result.info
            if info is None or (isinstance(info, str) and 'does not exist' in info.lower()):
                # 任务不存在
                return {
                    'status': 'not_found',
                    'message': f'任务 {task_id} 不存在',
                    'error': '任务不存在',
                    'task_id': task_id,
                    'progress': 0
                }
        
        if result.ready():
            if result.successful():
                # 获取任务结果
                task_result = result.result
                
                # 如果任务结果已经是标准格式（包含status字段），直接返回
                if isinstance(task_result, dict) and 'status' in task_result:
                    # 确保status字段是小写的，以保持一致性
                    task_result['status'] = task_result['status'].lower()
                    # 确保包含必要的字段
                    if 'progress' not in task_result:
                        task_result['progress'] = 100
                    return task_result
                else:
                    # 否则包装成标准格式
                    return {
                        'status': 'completed',
                        'message': '任务完成',
                        'result': task_result,
                        'task_id': task_id,
                        'progress': 100
                    }
            else:
                # 任务失败
                error_info = result.info or {}
                error_message = str(result.result) if result.result else '任务执行失败'
                
                # 如果错误信息中已经有status字段，直接返回
                if isinstance(error_info, dict) and 'status' in error_info:
                    error_info['status'] = error_info['status'].lower()
                    if 'progress' not in error_info:
                        error_info['progress'] = 0
                    return error_info
                else:
                    return {
                        'status': 'failed',
                        'message': error_message,
                        'error': error_message,
                        'task_id': task_id,
                        'progress': 0
                    }
        else:
            # 任务正在运行中，获取进度信息
            info = result.info or {}
            
            # 如果info中已经有status字段，直接使用
            if isinstance(info, dict) and 'status' in info:
                info['status'] = info['status'].lower()
                if 'progress' not in info:
                    info['progress'] = info.get('progress', 0)
                if 'task_id' not in info:
                    info['task_id'] = task_id
                return info
            else:
                # 根据任务状态返回相应的状态
                status_map = {
                    'PENDING': 'pending',
                    'STARTED': 'running',
                    'RETRY': 'running',
                    'REVOKED': 'failed'
                }
                status = status_map.get(task_state, 'running')
                
                return {
                    'status': status,
                    'progress': info.get('progress', 0) if isinstance(info, dict) else 0,
                    'message': info.get('message', '正在处理...') if isinstance(info, dict) else '任务正在运行中...',
                    'task_id': task_id
                }
            
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"获取任务状态异常: task_id={task_id}, error={str(e)}", exc_info=True)
        
        # 返回错误状态，但确保格式统一
        return {
            'status': 'failed',
            'message': f'查询任务状态失败: {str(e)}',
            'error': str(e),
            'task_id': task_id,
            'progress': 0
        }
