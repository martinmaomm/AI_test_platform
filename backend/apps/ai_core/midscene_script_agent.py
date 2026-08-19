"""
MidScene智能体
用于生成MidScene.js脚本的智能体
使用LangGraph调用视觉大模型完成任务
"""

import logging
import os
import re
from typing import TypedDict, List, Dict, Any, Optional, Callable, Union
from datetime import datetime
from django.conf import settings
from .models import LLMConfiguration
from langgraph.graph import StateGraph, END
from common.websocket import websocket_message_service, send_node_start_notification_helper
from .model_manager import get_vision_manager
from langchain_core.messages import HumanMessage, SystemMessage
logger = logging.getLogger(__name__)


# 定义Agent状态数据结构
class MidSceneAgentState(TypedDict):
    """MidScene脚本生成Agent的状态数据"""
    # 输入数据
    description: str                    # 用户需求描述
    screenshot_b64: str                 # 页面截图base64
    user_id: int                        # 用户ID
    
    # 文档内容
    llms_content: str                   # LLMs文档内容
    midscene_api_content: str           # Midscene API文档内容
    
    # 脚本相关
    script: Optional[str]               # 生成的MidScene.js脚本
    script_feedback: Optional[str]      # 脚本审核意见
    script_review_count: int            # 脚本审核次数
    
    # 控制参数
    retry_count: int                    # 当前重试次数
    max_retries: int                    # 最大重试次数
    current_step: str                   # 当前执行步骤


class MidSceneAgent:
    """MidScene脚本生成智能体"""
    
    def __init__(self, user, user_id: int = None, enable_streaming: bool = True, 
                 streaming_callback: Optional[Callable[[str, str], None]] = None):
        self.user = user
        self.user_id = user_id
        self.enable_streaming = enable_streaming
        self.streaming_callback = streaming_callback
        self.streaming_cache = {}
        
        # 初始化视觉模型管理器
        self.model_manager = self._initialize_vision_manager()
        
        # 构建LangGraph工作流
        self.workflow = self._build_workflow()
    
    
    def validate_script(self, script: str) -> bool:
        """验证脚本是否符合MidScene.js标准"""
        if not script:
            return False
        
        # 检查必要的导入语句
        has_step_import = "import { step" in script or "import {step" in script
        has_export_function = "export default async function run" in script
        has_await_step = "await step(" in script
        
        # 检查是否包含基本的MidScene.js结构
        has_midscene_structure = has_step_import and has_export_function and has_await_step
        
        logger.info(f"脚本验证结果: step_import={has_step_import}, export_function={has_export_function}, await_step={has_await_step}")
        
        return has_midscene_structure
    
    def _initialize_vision_manager(self):
        """初始化视觉模型管理器"""
        try:
            return get_vision_manager()
        except Exception as e:
            logger.error(f"视觉模型管理器初始化失败: {e}")
            raise RuntimeError(f"视觉模型管理器初始化失败: {e}") from e
    
    def _build_workflow(self) -> StateGraph:
        """构建LangGraph工作流"""
        # 创建状态图
        graph = StateGraph(MidSceneAgentState)
        
        # 添加所有节点
        graph.add_node("load_model_config", self._load_model_config_node)
        graph.add_node("read_documentation", self._read_documentation_node)
        graph.add_node("script_generator", self._script_generator_node)
        graph.add_node("script_reviewer", self._script_reviewer_node)
        graph.add_node("script_finalizer", self._script_finalizer_node)
        
        # 设置入口点
        graph.set_entry_point("load_model_config")
        
        # 添加条件边
        graph.add_conditional_edges(
            "load_model_config",
            self._decide_after_config_load,
            {
                "read_documentation": "read_documentation",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "read_documentation",
            self._decide_after_doc_read,
            {
                "script_generator": "script_generator",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "script_generator",
            self._decide_after_script_generation,
            {
                "script_reviewer": "script_reviewer",
                "script_finalizer": "script_finalizer",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "script_reviewer",
            self._decide_after_script_review,
            {
                "script_generator": "script_generator",
                "script_finalizer": "script_finalizer",
                "__end__": END
            }
        )
        
        graph.add_edge("script_finalizer", END)
        
        return graph.compile()
    
    def _send_websocket_message(self, message_type: str, content: str = "", step: str = "", 
                               node_name: str = "", node_display_name: str = "", 
                               task_id: str = None) -> bool:
        """通用WebSocket消息发送方法"""
        if not self.enable_streaming or not self.user_id:
            return False
        
        try:
            timestamp = datetime.now().isoformat()
            
            if message_type == "node_start":
                success = send_node_start_notification_helper(
                    user_id=self.user_id,
                    node_name=node_name,
                    node_display_name=node_display_name,
                    enable_streaming=self.enable_streaming,
                    room_type="midscene_script_generation",
                    task_id=task_id
                )
            elif message_type == "streaming":
                success = websocket_message_service.send_streaming_output(
                    user_id=self.user_id,
                    step=step,
                    content=content,
                    timestamp=timestamp,
                    room_type="midscene_script_generation"
                )
            else:
                logger.warning(f"未知的消息类型: {message_type}")
                return False
            
            if not success:
                logger.warning(f"WebSocket消息发送失败: [{message_type}] {step or node_name}")
            
            return success
            
        except Exception as e:
            logger.warning(f"WebSocket消息发送异常: {e}")
            return False
    
    def _send_node_start_notification(self, node_name: str, node_display_name: str):
        """发送节点开始执行通知（使用统一的辅助函数）"""
        return send_node_start_notification_helper(
            user_id=self.user_id,
            node_name=node_name,
            node_display_name=node_display_name,
            enable_streaming=self.enable_streaming,
            room_type="midscene_script_generation"
        )
    
    def _stream_vision_response(self, prompt: str, step_name: str, screenshot_b64: str = "") -> str:
        """流式调用视觉模型并处理响应"""
        if not self.enable_streaming:
            return self._call_vision_model(prompt, screenshot_b64)
        
        try:
            # 定义流式回调函数
            def streaming_callback(content: str):
                if self.streaming_callback:
                    self.streaming_callback(step_name, content)
                self._send_websocket_message("streaming", content, step_name)
            
            # 使用真正的流式调用视觉模型
            result = self.generate_midscene_script(
                natural_language=prompt,
                screenshot_b64=screenshot_b64,
                url=None,
                additional_context="请生成完整的MidScene.js脚本，包含必要的导入和函数定义",
                enable_streaming=True,
                streaming_callback=streaming_callback
            )
            
            if result.get('success'):
                script = result.get('script', '')
                # 发送流式输出完成消息
                self._send_websocket_message("streaming", "", step_name)
                return script
            else:
                error_msg = result.get('error', '未知错误')
                streaming_callback(f"❌ 脚本生成失败: {error_msg}")
                return ""
                
        except Exception as e:
            logger.error(f"流式调用视觉模型失败 [{step_name}]: {e}")
            # 回退到普通调用
            return self._call_vision_model(prompt, screenshot_b64)
    
    def _call_vision_model(self, prompt: str, screenshot_b64: str = "") -> str:
        """调用视觉模型（非流式）"""
        try:
            result = self.generate_midscene_script(
                natural_language=prompt,
                screenshot_b64=screenshot_b64,
                url=None,
                additional_context="请生成完整的MidScene.js脚本，包含必要的导入和函数定义"
            )
            return result.get('script', '') if result.get('success') else ''
        except Exception as e:
            logger.error(f"视觉模型调用失败: {e}")
            raise
    
    
    def _load_model_config_node(self, state: MidSceneAgentState) -> Dict[str, Any]:
        """1. 加载视觉模型配置节点"""
        self._send_node_start_notification("load_model_config", "加载视觉模型配置")
        
        try:
            test_result = self.model_manager.test_connection()
            if not test_result.get('success'):
                raise RuntimeError(f"视觉模型连接测试失败: {test_result.get('error')}")
            
            return {"current_step": "config_loaded"}
        except Exception as e:
            logger.error(f"加载视觉模型配置失败: {e}")
            return {
                "current_step": "config_load_failed",
                "script": None
            }
    
    
    def _read_documentation_node(self, state: MidSceneAgentState) -> Dict[str, Any]:
        """2. 读取文档内容节点"""
        self._send_node_start_notification("read_documentation", "读取文档内容")
        
        try:
            midscene_api_doc_path = os.path.join(settings.BASE_DIR, 'docs', 'Midscene API.md')
            midscene_api_content = self._read_file_safely(midscene_api_doc_path, "Midscene API文档")
            
            return {
                "llms_content": "",  # 不再使用LLMs文档
                "midscene_api_content": midscene_api_content,
                "current_step": "docs_loaded"
            }
        except Exception as e:
            logger.error(f"读取文档内容失败: {e}")
            return {
                "current_step": "doc_read_failed",
                "llms_content": "",
                "midscene_api_content": ""
            }
    
    def _read_file_safely(self, file_path: str, file_name: str) -> str:
        """安全读取文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"{file_name}文件未找到: {file_path}")
            return ""
    
    def _script_generator_node(self, state: MidSceneAgentState) -> Dict[str, Any]:
        """3. MidScene脚本生成节点"""
        self._send_node_start_notification("script_generator", "MidScene脚本生成")
        
        try:
            # 构建提示词
            prompt = self._build_vision_prompt(
                state['description'],
                state['midscene_api_content']
            )
            
            # 调用视觉模型生成脚本
            result = self.generate_midscene_script(
                natural_language=prompt,
                screenshot_b64=state.get('screenshot_b64', ''),
                url=None,
                additional_context="请生成完整的MidScene.js脚本，包含必要的导入和函数定义"
            )
            
            if not result.get('success'):
                raise RuntimeError(f"视觉模型生成失败: {result.get('error', '未知错误')}")
            
            # 清理和验证脚本
            raw_script = result.get('script', '')
            # 去掉markdown代码块和解释性文字
            if raw_script:
                # 去掉markdown代码块
                cleaned_script = re.sub(r"```[a-zA-Z]*", "", raw_script)
                cleaned_script = cleaned_script.replace("```", "")
                # 去掉解释性前后缀和注释行
                lines = []
                for line in cleaned_script.splitlines():
                    stripped = line.strip()
                    # 跳过空行和注释行
                    if stripped and not stripped.startswith("//") and not stripped.startswith("#"):
                        lines.append(line)
                cleaned_script = "\n".join(lines).strip()
            else:
                cleaned_script = ""
            
            if self.validate_script(cleaned_script):
                logger.info("脚本生成成功并通过验证")
                return {
                    "script": cleaned_script,
                    "current_step": "script_generated",
                    "script_review_count": 0
                }
            else:
                logger.warning("脚本生成成功但验证失败，需要重新生成")
                return {
                    "script": cleaned_script,
                    "current_step": "script_validation_failed",
                    "script_review_count": 0
                }
                
        except Exception as e:
            logger.error(f"MidScene脚本生成失败: {e}")
            return {
                "current_step": "script_generation_failed",
                "script": None
            }
    
    def _script_reviewer_node(self, state: MidSceneAgentState) -> Dict[str, Any]:
        """4. 脚本审核节点（仅本地验证）"""
        self._send_node_start_notification("script_reviewer", "脚本审核")
        
        try:
            review_count = state.get("script_review_count", 0) + 1
            current_script = state['script']
            
            # 进行本地验证
            if self.validate_script(current_script):
                logger.info("脚本通过本地验证，审核通过")
                return {
                    "current_step": "script_approved",
                    "script_review_count": review_count
                }
            else:
                logger.warning(f"脚本本地验证失败 (第{review_count}次)，需要重新生成")
                return {
                    "current_step": "script_needs_revision",
                    "script_review_count": review_count
                }
                
        except Exception as e:
            logger.error(f"脚本审核失败: {e}")
            return {
                "current_step": "script_review_failed",
                "script": None
            }
    
    def _script_finalizer_node(self, state: MidSceneAgentState) -> Dict[str, Any]:
        """5. 脚本最终化节点"""
        self._send_node_start_notification("script_finalizer", "脚本最终化")
        
        try:
            script = state.get('script', '')
            if not script:
                raise RuntimeError("脚本内容为空")
            
            # 最终验证脚本
            if not self.validate_script(script):
                logger.warning("最终脚本验证失败，但继续保存")
            
            # 保存为本地ts文件
            output_dir = os.path.join(settings.BASE_DIR, "outputs")
            os.makedirs(output_dir, exist_ok=True)
            
            timestamp = int(datetime.now().timestamp())
            filename = f"midscene_{timestamp}.ts"
            output_path = os.path.join(output_dir, filename)
            
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(script)
            
            logger.info(f"脚本已保存到: {output_path}")
            
            return {
                "script": script,
                "current_step": "script_finalized",
                "output_file": output_path,
                "filename": filename
            }
            
        except Exception as e:
            logger.error(f"脚本最终化失败: {e}")
            return {
                "current_step": "script_finalization_failed",
                "script": state.get('script', ''),
                "output_file": None,
                "filename": None
            }
    
    
    
    def _build_vision_prompt(self, description: str, midscene_api_content: str) -> str:
        """构建视觉模型的提示词"""
        return f"""你是一名专业的 UI 自动化测试脚本工程师。  
你的任务是根据用户的自然语言需求、页面截图/界面描述，以及 Midscene.js 的 API 说明文档，自动生成符合 Midscene.js 标准的 **TypeScript 脚本**。  

【用户需求】  
{description}

【Midscene API 文档】  
{midscene_api_content}

【生成要求】  
1. 严格参考 Midscene.js 的 API 定义来编写 TypeScript 脚本。  
2. 输出必须是一个完整的、可执行的 midscene.js TypeScript 脚本。  
3. 如果需求涉及多个步骤，请用 `await step()` 顺序编排。  
4. 保证脚本能直接运行在 midscene.js 的执行环境中。  

⚠️ 重要输出规范：
- 你必须只输出 TypeScript 代码，不要包含任何 Markdown 格式（如 ```ts 或 ```）
- 不要输出解释文字、注释、额外说明
- 保证脚本可以在 MidScene.js 环境中直接运行
- 必须包含 import {{ step }} from "@midscene/core"
- 必须包含 export default async function run()
- 必须包含至少一个 await step() 调用

【输出格式示例】  
import {{ step, click, input }} from "@midscene/core";

export default async function run() {{
  await step("点击登录按钮", async () => {{
    await click("#login-button");
  }});

  await step("输入用户名", async () => {{
    await input("#username", "test_user");
  }});

  await step("输入密码", async () => {{
    await input("#password", "123456");
  }});

  await step("点击提交按钮", async () => {{
    await click("#submit");
  }});
}}

重要：请直接返回纯TypeScript代码，不要使用```typescript或```等代码块标记，不要包含任何解释文字或注释。"""
    
    
    def _decide_after_config_load(self, state: MidSceneAgentState) -> str:
        """配置加载后的决策"""
        if state.get("current_step") == "config_loaded":
            return "read_documentation"
        else:
            return "__end__"
    
    def _decide_after_doc_read(self, state: MidSceneAgentState) -> str:
        """文档读取后的决策"""
        if state.get("current_step") == "docs_loaded":
            return "script_generator"
        else:
            return "__end__"
    
    def _decide_after_script_generation(self, state: MidSceneAgentState) -> str:
        """脚本生成后的决策"""
        current_step = state.get("current_step")
        
        if current_step == "script_generated":
            return "script_reviewer"
        elif current_step == "script_validation_failed":
            # 脚本验证失败，重新生成
            return "script_generator"
        elif current_step == "script_generation_failed":
            return "__end__"
        else:
            return "script_finalizer"
    
    def _decide_after_script_review(self, state: MidSceneAgentState) -> str:
        """脚本审核后的决策"""
        current_step = state.get("current_step")
        review_count = state.get("script_review_count", 0)
        max_reviews = 3  # 最大审核次数
        
        if current_step == "script_approved":
            return "script_finalizer"
        elif review_count >= max_reviews:
            logger.warning(f"已达到最大审核次数({max_reviews})，强制通过审核")
            return "script_finalizer"  # 强制通过，进入最终化阶段
        else:
            return "script_generator"  # 需要重新生成脚本
    
    def run(self, description: str, screenshot_b64: str = "") -> Dict[str, Any]:
        """运行MidScene脚本生成智能体"""
        try:
            
            # 强制使用LangGraph工作流
            if not self.workflow:
                raise RuntimeError("LangGraph工作流未初始化，无法运行MidScene脚本生成智能体")
            
            return self._run_with_langgraph(description, screenshot_b64)
                
        except Exception as e:
            error_msg = f"运行MidScene脚本生成智能体失败: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "current_step": "failed"
            }
    
    def _run_with_langgraph(self, description: str, screenshot_b64: str) -> Dict[str, Any]:
        """使用LangGraph工作流运行"""
        # 初始化状态
        initial_state = {
            "description": description,
            "screenshot_b64": screenshot_b64,
            "user_id": self.user_id,
            "llms_content": "",
            "midscene_api_content": "",
            "script": None,
            "script_feedback": None,
            "script_review_count": 0,
            "retry_count": 0,
            "max_retries": 3,
            "current_step": "initialized"
        }
        
        # 运行工作流
        result = self.workflow.invoke(initial_state)
        
        # 检查是否有错误
        if not result.get("script"):
            return {
                "success": False,
                "error": "脚本生成失败",
                "current_step": result.get("current_step", "unknown")
            }
        
        # 返回成功结果
        return {
            "success": True,
            "script": result.get("script"),
            "model_info": self.model_manager.get_model_info(),
            "model_type": "vision",
            "current_step": result.get("current_step", "completed"),
            "output_file": result.get("output_file"),
            "filename": result.get("filename")
        }
    
    def generate_midscene_script(self, natural_language: str, screenshot_b64: str = "", url: Optional[str] = None, additional_context: str = "", enable_streaming: bool = False, streaming_callback: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """生成MidScene.js脚本（视觉模型专用）"""
        if not self.model_manager:
            error_msg = "没有可用的视觉模型，请先配置视觉模型"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        try:
            # 构建包含图片的消息
            messages = self._build_vision_messages(natural_language, screenshot_b64, url, additional_context)
            
            if enable_streaming and streaming_callback:
                # 使用流式调用
                response = self.model_manager.stream_invoke(messages, callback=streaming_callback)
            else:
                # 使用普通调用
                response = self.model_manager.invoke(messages)
            
            return {
                'success': True,
                'script': response,
                'model_info': self.model_manager._get_current_llm_model_info()
            }
            
        except Exception as e:
            logger.error(f"视觉模型调用失败: {e}")
            raise RuntimeError(f"视觉模型调用失败: {e}") from e
    
    def _build_vision_messages(self, natural_language: str, screenshot_b64: str = "", url: Optional[str] = None, additional_context: str = "") -> List[Union[HumanMessage, SystemMessage]]:
        """构建包含图片的视觉消息（参考通义千问官方示例）"""
        
        # 系统提示词
        system_content = [{
            "type": "text",
            "text": """你是一名专业的 UI 自动化测试脚本工程师。  
你的任务是根据用户的自然语言需求、页面截图/界面描述，以及 Midscene.js 的 API 说明文档，自动生成符合 Midscene.js 标准的 **TypeScript 脚本**。  

【生成要求】  
1. 严格参考 Midscene.js 的 API 定义来编写 TypeScript 脚本。  
2. 输出必须是一个完整的、可执行的 midscene.js TypeScript 脚本。  
3. 如果需求涉及多个步骤，请用 `await step()` 顺序编排。  
4. 保证脚本能直接运行在 midscene.js 的执行环境中。  

⚠️ 重要输出规范：
- 你必须只输出 TypeScript 代码，不要包含任何 Markdown 格式（如 ```ts 或 ```）
- 不要输出解释文字、注释、额外说明
- 保证脚本可以在 MidScene.js 环境中直接运行
- 必须包含 import { step } from "@midscene/core"
- 必须包含 export default async function run()
- 必须包含至少一个 await step() 调用

【输出格式示例】  
import { step, click, input } from "@midscene/core";

export default async function run() {
  await step("点击登录按钮", async () => {
    await click("#login-button");
  });

  await step("输入用户名", async () => {
    await input("#username", "test_user");
  });

  await step("输入密码", async () => {
    await input("#password", "123456");
  });

  await step("点击提交按钮", async () => {
    await click("#submit");
  });
}

重要：请直接返回纯TypeScript代码，不要使用```typescript或```等代码块标记，不要包含任何解释文字或注释。"""
        }]
        
        # 构建用户消息内容
        user_content = []
        
        # 添加截图（如果有）- 使用官方示例的格式
        if screenshot_b64:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_b64}"
                }
            })
        
        # 构建文本描述
        text_parts = [f"【用户需求】\n{natural_language}"]
        
        # 添加URL信息
        if url:
            text_parts.append(f"\n【目标URL】\n{url}")
        
        # 添加上下文信息
        if additional_context:
            text_parts.append(f"\n【额外上下文】\n{additional_context}")
        
        # 添加文本内容
        user_content.append({
            "type": "text",
            "text": "\n".join(text_parts)
        })
        
        return [
            SystemMessage(content=system_content),
            HumanMessage(content=user_content)
        ]


def create_midscene_agent(user, user_id: int = None, enable_streaming: bool = True,
                          streaming_callback: Optional[Callable[[str, str], None]] = None) -> MidSceneAgent:
    """创建MidScene智能体实例"""
    return MidSceneAgent(user, user_id, enable_streaming, streaming_callback)
