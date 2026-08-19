"""
多模型兼容管理器
支持OpenAI、DeepSeek、本地Ollama等LLM模型
支持Qwen-VL、UI-TARS、Doubao Vision、Gemini、GPT-4V等视觉模型
使用 LangChain 1.0+ 的 init_chat_model 统一接口初始化所有模型
"""
import json
import logging
import traceback
import requests
from typing import Dict, List, Any, Optional, Callable, Union
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.chat_models import init_chat_model
import time
from datetime import datetime

from .models import LLMConfiguration
from common.websocket import websocket_message_service

logger = logging.getLogger(__name__)


class ModelManager:
    """多模型兼容管理器（支持LLM和视觉模型）"""
    
    def __init__(self, model_type: str = 'llm'):
        """
        初始化模型管理器
        
        Args:
            model_type: 模型类型，'llm' 或 'vision'
        """
        self.model_type = model_type
        self._initialized = False
        
        if model_type not in ['llm', 'vision']:
            raise ValueError(f"不支持的模型类型: {model_type}")
        
        self.config = self._load_config()
        self._initialized = True
        self._initialize_model()
        logger.info(f"{model_type}管理器初始化完成")

    
    def _load_config(self) -> Dict[str, Any]:
        """从数据库加载配置"""
        active_config = LLMConfiguration.objects.filter(
            model_type=self.model_type, is_active=True
        ).order_by('-created_at').first()
        
        if active_config:
            logger.info(f"从数据库加载{self.model_type}配置: {active_config.provider} - {active_config.model_name}")
            return self._config_from_db(active_config)
        
        any_config = LLMConfiguration.objects.filter(model_type=self.model_type).first()
        if any_config:
            logger.warning(f"从数据库加载{self.model_type}配置（已禁用）: {any_config.provider} - {any_config.model_name}")
            return self._config_from_db(any_config)
        
        error_msg = f"数据库中没有可用的{self.model_type}配置，请先创建并配置{self.model_type}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    def _config_from_db(self, config: 'LLMConfiguration') -> Dict[str, Any]:
        """从数据库配置对象创建配置字典"""
        return {
            'provider': config.provider,
            'enabled': True,
            'api_key': config.api_key,
            'base_url': config.base_url,
            'model': config.model_name,
            'extra_config': config.extra_config or {}
        }
    
    def _initialize_model(self):
        """
        初始化模型（基于 LangChain 官方文档的最佳实践）
        
        使用 init_chat_model 统一接口初始化各种模型提供商：
        - DeepSeek: 使用 langchain-deepseek 专用 provider
        - OpenAI 兼容 API（OpenAI, Qwen, Ernie, Zhipu 等）
        - Ollama 本地模型
        - Anthropic Claude
        - Google Gemini
        - 其他支持的提供商
        
        参考文档: https://docs.langchain.com/oss/python/langchain/models
        """
        provider = self.config['provider']
        model_name = self.config['model']
        extra_config = self.config.get('extra_config', {})
        
        # 构建基础初始化参数
        init_params = {
            'model': model_name,
            'max_tokens': 8192,  # 强制解除输出截断限制，拉满大模型肺活量
        }
        
        # 根据提供商设置 model_provider 和特定参数
        if provider == 'ollama':
            # Ollama 本地模型
            if not self._check_ollama_service(self.config.get('base_url', 'http://localhost:11434')):
                raise RuntimeError("Ollama服务不可用，请检查服务是否运行")
            
            init_params.update({
                'model_provider': 'ollama',
                'base_url': self.config.get('base_url') or 'http://localhost:11434',
                'timeout': extra_config.get('timeout', 30),
            })
            self.llm_type = 'ollama'
            
        elif provider == 'deepseek':
            # DeepSeek 使用 langchain-deepseek 专用 provider
            # 参考文档: https://docs.langchain.com/oss/python/integrations/providers/deepseek
            init_params.update({
                'model_provider': 'deepseek',
                'api_key': self.config.get('api_key'),
                'base_url': self.config.get('base_url'),  # 可选，支持自定义 base_url
                'timeout': extra_config.get('timeout', 300),
                'max_retries': extra_config.get('max_retries', 3),
            })
            self.llm_type = 'deepseek'
            
        elif provider in ['openai', 'qwen', 'ernie', 'zhipu']:
            # OpenAI 兼容 API 的提供商
            # 这些提供商可以使用 OpenAI 兼容接口，通过 base_url 区分
            init_params.update({
                'model_provider': 'openai',  # 使用 OpenAI 兼容接口
                'api_key': self.config.get('api_key'),
                'base_url': self.config.get('base_url'),
                'timeout': extra_config.get('timeout', 300),
                'max_retries': extra_config.get('max_retries', 3),
            })
            self.llm_type = provider
            
        elif provider == 'anthropic':
            # Anthropic Claude
            init_params.update({
                'model_provider': 'anthropic',
                'api_key': self.config.get('api_key'),
                'timeout': extra_config.get('timeout', 300),
                'max_retries': extra_config.get('max_retries', 3),
            })
            self.llm_type = 'anthropic'
            
        elif provider == 'google_genai':
            # Google Gemini
            init_params.update({
                'model_provider': 'google_genai',
                'api_key': self.config.get('api_key'),
                'timeout': extra_config.get('timeout', 300),
                'max_retries': extra_config.get('max_retries', 3),
            })
            self.llm_type = 'google_genai'
            
        else:
            # 其他提供商，尝试使用 OpenAI 兼容接口
            logger.warning(f"未知的提供商 {provider}，尝试使用 OpenAI 兼容接口")
            init_params.update({
                'model_provider': 'openai',
                'api_key': self.config.get('api_key'),
                'base_url': self.config.get('base_url'),
                'timeout': extra_config.get('timeout', 300),
                'max_retries': extra_config.get('max_retries', 3),
            })
            self.llm_type = provider
        
        # 从 extra_config 中读取其他通用参数
        common_params = ['temperature', 'max_tokens', 'top_p', 'top_k', 'streaming']
        for param in common_params:
            if param in extra_config:
                init_params[param] = extra_config[param]
        
        # 过滤掉 None 值，避免传递给 init_chat_model
        filtered_params = {k: v for k, v in init_params.items() if v is not None}
        
        try:
            # 使用 LangChain 官方推荐的 init_chat_model 初始化模型
            self.current_llm = init_chat_model(**filtered_params)
            logger.info(
                f"{provider} {self.model_type}初始化成功，"
                f"模型: {model_name}, "
                f"提供商: {filtered_params.get('model_provider', 'auto')}"
            )
        except ValueError as e:
            # 模型名称或提供商无效
            logger.error(f"{provider}模型配置错误: {e}", exc_info=True)
            raise RuntimeError(
                f"模型配置错误: {str(e)}。请检查模型名称和提供商是否正确"
            ) from e
        except Exception as e:
            # 其他初始化错误（网络、认证等）
            logger.error(f"{provider}初始化失败: {e}", exc_info=True)
            raise RuntimeError(
                f"当前{self.model_type}配置不可用: {str(e)}。"
                f"请检查配置、网络连接或API密钥"
            ) from e
    
    def _check_ollama_service(self, base_url: str) -> bool:
        """检查Ollama服务是否可用"""
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False
    
    def _ensure_initialized(self):
        """确保模型已初始化"""
        if not self._initialized or not hasattr(self, 'current_llm') or not self.current_llm:
            self._initialized = True
            self._initialize_model()
    
    def invoke(self, messages: List[Union[HumanMessage, SystemMessage]], **kwargs) -> str:
        """调用LLM"""
        self._ensure_initialized()
        try:
            response = self.current_llm.invoke(messages, **kwargs)
            return self._extract_response_content(response)
        except Exception as e:
            logger.error(f"LLM调用失败: {e}", exc_info=True)
            raise RuntimeError(f"LLM调用失败: {e}") from e
    
    def stream_invoke(self, messages: List[Union[HumanMessage, SystemMessage]], 
                     callback: Optional[Callable[[str], None]] = None, **kwargs) -> str:
        """流式调用LLM"""
        self._ensure_initialized()
        try:
            return self._stream_invoke_unified(messages, callback, **kwargs)
        except Exception as e:
            logger.warning(f"流式调用失败，回退到普通调用: {e}")
            return self.invoke(messages, **kwargs)
    
    def _stream_invoke_unified(self, messages: List[Union[HumanMessage, SystemMessage]], 
                               callback: Optional[Callable[[str], None]] = None, **kwargs) -> str:
        """统一的流式调用方法（适用于所有通过 init_chat_model 初始化的 ChatModel）"""
        if not hasattr(self.current_llm, 'stream'):
            logger.warning("当前LLM不支持流式输出，回退到普通调用")
            return self.invoke(messages, **kwargs)
        
        full_response = ""
        for chunk in self.current_llm.stream(messages, **kwargs):
            # LangChain 1.0+ 中，ChatModel返回的消息块有content属性
            # 只提取content内容，忽略其他元数据
            if isinstance(chunk, str):
                content = chunk
            elif hasattr(chunk, 'content'):
                content = chunk.content
            else:
                # 如果chunk没有content属性，跳过这个chunk
                continue
            
            # 只处理非空的字符串内容
            if content and isinstance(content, str) and content.strip():
                full_response += content
                if callback:
                    callback(content)
        
        return full_response
    
    def _extract_response_content(self, response) -> str:
        """提取响应内容"""
        return getattr(response, 'content', getattr(response, 'text', str(response)))
    
    def _send_websocket_message(self, user_id: int, step_name: str, content: str, room_type: str) -> bool:
        """发送WebSocket消息的辅助方法"""
        try:
            timestamp = datetime.now().isoformat()
            success = websocket_message_service.send_streaming_output(
                user_id=user_id,
                step=step_name,
                content=content,
                timestamp=timestamp,
                room_type=room_type
            )
            if not success:
                logger.warning(f"WebSocket消息发送失败: [{step_name}]")
            return success
        except Exception as e:
            logger.warning(f"WebSocket发送失败: {e}")
            return False
    
    def stream_invoke_with_websocket(
        self,
        messages: List[Union[HumanMessage, SystemMessage]],
        step_name: str,
        enable_streaming: bool = True,
        user_id: Optional[int] = None,
        streaming_callback: Optional[Callable[[str, str], None]] = None,
        room_type: str = "default"
    ) -> str:
        """
        流式调用LLM并通过WebSocket发送输出
        
        Args:
            messages: LLM消息列表
            step_name: 步骤名称
            enable_streaming: 是否启用流式输出
            user_id: 用户ID（用于WebSocket发送）
            streaming_callback: 自定义流式回调函数
            room_type: WebSocket房间类型
            
        Returns:
            LLM响应文本
        """
        if not enable_streaming:
            return self.invoke(messages)
        
        try:
            def internal_callback(content: str):
                if streaming_callback:
                    streaming_callback(step_name, content)
                if user_id:
                    self._send_websocket_message(user_id, step_name, content, room_type)
                logger.debug(f"流式输出 [{step_name}]: {content}")
            
            return self.stream_invoke(messages, callback=internal_callback)
        except Exception as e:
            logger.error(f"流式调用LLM失败 [{step_name}]: {e}")
            try:
                return self.invoke(messages)
            except Exception as fallback_error:
                logger.error(f"回退调用也失败 [{step_name}]: {fallback_error}")
                raise
    
    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        self._ensure_initialized()
        return {
            'type': self.llm_type,
            'available_models': self._get_available_models(),
            'current_model': self._get_current_model_info()
        }
    
    
    def _get_available_models(self) -> List[str]:
        """获取可用的模型列表"""
        return [f"{self.config['provider']}: {self.config['model']}"] if self.config else []
    
    def _get_current_model_info(self) -> Dict[str, Any]:
        """获取当前模型信息"""
        if not self.current_llm or not self.config:
            return {'type': 'None', 'model': f'No {self.model_type} configured'}
        return {'type': self.llm_type, 'model': self.config['model']}
    
    def _reload_model(self):
        """重新加载模型配置（内部方法）"""
        self._reset_state()
        self.config = self._load_config()
        self._initialize_model()
    
    def reload_config(self):
        """重新从数据库加载配置并重新初始化模型"""
        logger.info(f"重新从数据库加载{self.model_type}配置")
        self._reload_model()
        logger.info(f"{self.model_type}配置重新加载完成，当前类型: {self.llm_type}")
    
    def switch_to_config(self, config_id: int) -> bool:
        """切换到指定的配置"""
        config = LLMConfiguration.objects.filter(id=config_id, is_active=True).first()
        if not config:
            logger.error(f"配置 {config_id} 不存在或未激活")
            return False
        
        if config.model_type != self.model_type:
            logger.error(f"配置 {config_id} 的模型类型 {config.model_type} 与当前管理器类型 {self.model_type} 不匹配")
            return False
        
        try:
            logger.info(f"切换到{self.model_type}配置: {config.provider} - {config.model_name}")
            self._reset_state()
            self.config = self._config_from_db(config)
            self._initialize_model()
            logger.info(f"{self.model_type}配置切换完成，当前类型: {self.llm_type}")
            return True
        except Exception as e:
            logger.error(f"切换{self.model_type}配置失败: {e}", exc_info=True)
            return False
    
    def _reset_state(self):
        """重置管理器状态"""
        self._initialized = False
        self.current_llm = None
        self.llm_type = None
    
    def test_connection(self) -> Dict[str, Any]:
        """测试模型连接"""
        try:
            self._ensure_initialized()
            start_time = time.time()
            
            test_messages = [
                SystemMessage(content="你是一个有用的AI助手。请用一句话回复。"),
                HumanMessage(content="你好，请简单介绍一下你自己。")
            ]
            response = self.current_llm.invoke(test_messages)
            response_content = self._extract_response_content(response)
            
            return {
                'success': True,
                'message': f'{self.model_type}连接测试成功',
                'response_time': round(time.time() - start_time, 2),
                'response_content': response_content[:100] + '...' if len(response_content) > 100 else response_content,
                'model_info': self._get_current_model_info()
            }
        except Exception as e:
            logger.error(f"模型连接测试失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': f'连接测试失败: {str(e)}',
                'error_details': traceback.format_exc()
            }


# 移除全局实例，改为按需创建
def get_llm_manager() -> ModelManager:
    """获取LLM管理器实例（向后兼容）"""
    return ModelManager(model_type='llm')

def get_vision_manager() -> ModelManager:
    """获取视觉模型管理器实例"""
    return ModelManager(model_type='vision')

def get_model_manager(model_type: str = 'llm') -> ModelManager:
    """获取模型管理器实例"""
    return ModelManager(model_type=model_type)
