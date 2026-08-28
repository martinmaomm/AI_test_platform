"""
WebUI MidScene智能体
用于生成WebUI测试脚本的智能体
使用mcp-use集成MCP访问能力，支持流式输出
"""

import logging
import asyncio
import yaml
import re
import os
from typing import TypedDict, List, Dict, Any, Optional, Callable
from datetime import datetime
from django.conf import settings
from .models import MCPConfiguration
from langgraph.graph import StateGraph, END
from common.websocket import websocket_message_service, send_node_start_notification_helper
from common.parsers import extract_javascript_from_output, validate_javascript_content, is_javascript_line
from common.parsers import extract_yaml_from_output, validate_yaml_content
from .model_manager import get_vision_manager
from mcp_use import MCPClient, MCPAgent

logger = logging.getLogger(__name__)


# 定义WebUI MidScene Agent状态数据结构
class WebUIMidSceneAgentState(TypedDict):
    """WebUI MidScene测试脚本生成Agent的状态数据"""
    description: str                    # 用户需求描述
    url: str                           # 目标URL
    user_id: int                       # 用户ID
    project_id: Optional[int]          # 项目ID
    script_name: Optional[str]         # 脚本名称
    mcp_config: Dict[str, Any]         # MCP服务器配置
    yaml_test_script: Optional[str]     # 生成的yaml测试脚本
    test_script: Optional[str]          # 转换后的JavaScript测试脚本
    script_id: Optional[int]            # 保存的脚本ID
    current_step: str                   # 当前执行步骤


class WebUIMidSceneAgent:
    """WebUI MidScene测试脚本生成智能体"""
    
    def __init__(self, user_id: int, user=None, enable_streaming: bool = True):
        self.user = user
        self.user_id = user_id
        self.enable_streaming = enable_streaming
        self.project_id = None
        self.script_name = None
        self.mcp_config = {}
        
        # 初始化视觉模型管理器
        try:
            self.vision_manager = get_vision_manager()
            logger.info(f"视觉模型管理器初始化成功: {self.vision_manager.get_model_info()}")
        except Exception as e:
            logger.error(f"视觉模型管理器初始化失败: {e}")
            raise RuntimeError(f"视觉模型管理器初始化失败: {e}") from e
        
        # 初始化MCP客户端
        self.mcp_client = None
        self.mcp_agent = None
        
        # 构建LangGraph工作流
        self.workflow = self._build_workflow()
    
    
    def _initialize_mcp_client(self, config: Dict[str, Any]) -> MCPClient:
        """初始化MCP客户端"""
        try:
            # 创建MCP客户端
            client = MCPClient.from_dict(config)
            return client
        except Exception as e:
            logger.error(f"MCP客户端初始化失败: {e}")
            raise RuntimeError(f"MCP客户端初始化失败: {e}") from e
    
    def _initialize_mcp_agent(self, client: MCPClient) -> MCPAgent:
        """初始化MCP智能体，启用详细输出"""
        try:
            # 获取视觉模型实例
            vision_model = self.vision_manager.current_llm
            
            if not vision_model:
                raise RuntimeError("视觉模型未初始化")
            
            # 记录使用的视觉模型信息
            model_info = self.vision_manager.get_model_info()
            logger.info(f"使用视觉模型: {model_info}")
            
            # 创建MCP智能体，启用详细输出
            agent = MCPAgent(
                llm=vision_model,
                client=client,
                max_steps=30
            )
            
            logger.info("MCP智能体初始化成功，启用详细输出")
            return agent
        except Exception as e:
            logger.error(f"MCP智能体初始化失败: {e}")
            raise RuntimeError(f"MCP智能体初始化失败: {e}") from e
    
    def _build_workflow(self) -> StateGraph:
        """构建LangGraph工作流"""
        # 创建状态图
        graph = StateGraph(WebUIMidSceneAgentState)
        
        # 添加所有节点
        graph.add_node("load_mcp_config", self._load_mcp_config_node)
        graph.add_node("initialize_mcp", self._initialize_mcp_node)
        graph.add_node("call_mcp", self._call_mcp_node)
        graph.add_node("convert_yaml_to_js", self._convert_yaml_to_js_node)
        graph.add_node("save_script", self._save_script_node)
        
        # 设置入口点
        graph.set_entry_point("load_mcp_config")
        
        # 添加条件边
        graph.add_conditional_edges(
            "load_mcp_config",
            self._decide_after_config_load,
            {
                "initialize_mcp": "initialize_mcp",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "initialize_mcp",
            self._decide_after_mcp_init,
            {
                "call_mcp": "call_mcp",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "call_mcp",
            self._decide_after_mcp_call,
            {
                "convert_yaml_to_js": "convert_yaml_to_js",
                "__end__": END
            }
        )
        
        graph.add_conditional_edges(
            "convert_yaml_to_js",
            self._decide_after_conversion,
            {
                "save_script": "save_script",
                "__end__": END
            }
        )
        
        graph.add_edge("save_script", END)
        
        return graph.compile()
    
    def _send_websocket_message(self, content: str, step: str = ""):
        """发送WebSocket流式消息"""
        if not self.enable_streaming or not self.user_id:
            return False
        
        try:
            timestamp = datetime.now().isoformat()
            return websocket_message_service.send_streaming_output(
                user_id=self.user_id,
                step=step,
                content=content,
                timestamp=timestamp,
                room_type="webui_test_generation"
            )
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
            room_type="webui_test_generation"
        )
    
    
    
    def _load_mcp_config_node(self, state: WebUIMidSceneAgentState) -> Dict[str, Any]:
        """1. 加载MCP配置节点"""
        self._send_node_start_notification("load_mcp_config", "加载MCP配置")
        
        try:
            # 获取用户ID
            user_id = state.get("user_id")
            
            # 发送开始加载的消息
            self._send_websocket_message("🔍 开始加载MCP配置...\n", "加载MCP配置")
            
            # 构建查询条件
            query_filter = {'is_active': True}
            if user_id:
                query_filter['created_by_id'] = user_id
            
            # 查询启用的MCP配置
            mcp_configs = MCPConfiguration.objects.filter(**query_filter)
            
            if not mcp_configs.exists():
                logger.info(f"用户 {user_id} 没有找到启用的MCP配置")
                mcp_config = {"mcpServers": {}}
                self._send_websocket_message(f"⚠️ 用户 {user_id} 没有找到启用的MCP配置\n", "加载MCP配置")
            else:
                logger.info(f"找到 {mcp_configs.count()} 个启用的MCP配置")
                
                # 发送找到配置的消息
                self._send_websocket_message(f"📋 找到 {mcp_configs.count()} 个启用的MCP配置\n", "加载MCP配置")
                
                # 查找包含mcp-midscene且激活的配置
                midscene_config = None
                for config in mcp_configs:
                    try:
                        config_dict = config.get_config_dict()
                        mcp_servers = config_dict.get('mcpServers', {})
                        
                        # 检查是否包含mcp-midscene
                        if 'mcp-midscene' not in mcp_servers:
                            continue
                        
                        # 检查mcp-midscene是否激活
                        midscene_config_data = mcp_servers['mcp-midscene']
                        is_active = midscene_config_data.get('is_active', True)  # 默认为激活状态
                        
                        if not is_active:
                            logger.warning(f"MCP配置 {config.id} 中的mcp-midscene未激活")
                            continue
                        
                        midscene_config = config
                        logger.info(f"找到mcp-midscene配置: {config.id}")
                        break
                        
                    except Exception as e:
                        logger.warning(f"解析MCP配置 {config.id} 失败: {e}")
                        continue
                
                # 构建MCP配置
                if midscene_config:
                    try:
                        config_dict = midscene_config.get_config_dict()
                        mcp_config = {"mcpServers": config_dict['mcpServers']}
                        logger.info(f"成功加载mcp-midscene配置: {list(config_dict['mcpServers'].keys())}")
                        
                        # 发送成功消息
                        self._send_websocket_message(f"✅ 成功加载mcp-midscene配置: {list(config_dict['mcpServers'].keys())}\n", "加载MCP配置")
                    except Exception as e:
                        logger.error(f"构建MCP配置失败: {e}")
                        mcp_config = {"mcpServers": {}}
                        # 发送错误消息
                        self._send_websocket_message(f"❌ 构建MCP配置失败: {str(e)}\n", "加载MCP配置")
                else:
                    logger.warning("没有找到mcp-midscene配置")
                    mcp_config = {"mcpServers": {}}
                    # 发送警告消息
                    self._send_websocket_message("⚠️ 没有找到mcp-midscene配置\n", "加载MCP配置")
                
        except Exception as e:
            logger.error(f"加载MCP配置失败: {e}")
            mcp_config = {"mcpServers": {}}
            # 发送错误消息
            self._send_websocket_message(f"❌ 加载MCP配置失败: {str(e)}\n", "加载MCP配置")
        
        return {
            "mcp_config": mcp_config,
            "current_step": "config_loaded"
        }
    
    def _initialize_mcp_node(self, state: WebUIMidSceneAgentState) -> Dict[str, Any]:
        """2. 初始化MCP客户端和智能体节点"""
        self._send_node_start_notification("initialize_mcp", "初始化MCP客户端")
        
        try:
            # 验证MCP配置
            mcp_config = state.get("mcp_config", {})
            if not mcp_config:
                raise RuntimeError("MCP配置为空")
            
            # 发送开始初始化的消息
            self._send_websocket_message("🔧 开始初始化MCP客户端...\n", "初始化MCP客户端")
            
            # 初始化MCP客户端
            self.mcp_client = self._initialize_mcp_client(mcp_config)
            
            # 发送客户端初始化成功消息
            self._send_websocket_message("✅ MCP客户端初始化成功\n", "初始化MCP客户端")
            self._send_websocket_message("🔗 开始创建MCP会话...\n", "初始化MCP客户端")
            
            # 创建MCP会话
            self._create_mcp_sessions()
            
            # 初始化MCP智能体
            self.mcp_agent = self._initialize_mcp_agent(self.mcp_client)
            
            return {
                "current_step": "mcp_initialized"
            }
        except Exception as e:
            logger.error(f"初始化MCP失败: {e}")
            # 发送错误消息
            self._send_websocket_message(f"❌ 初始化MCP失败: {str(e)}\n", "初始化MCP客户端")
            return {
                "current_step": "mcp_init_failed"
            }
    
    def _create_mcp_sessions(self):
        """创建MCP会话"""
        try:
            # 检查是否有运行的事件循环
            try:
                asyncio.get_running_loop()
                # 在运行的事件循环中，使用线程池来运行异步任务
                self._create_sessions_in_thread()
            except RuntimeError:
                # 没有运行的事件循环，使用asyncio.run
                asyncio.run(self.mcp_client.create_all_sessions())
                logger.info("MCP会话创建完成")
        except Exception as e:
            logger.error(f"MCP会话创建失败: {e}")
            # 即使会话创建失败，也继续执行，只是没有工具可用
    
    def _create_sessions_in_thread(self):
        """在线程中创建MCP会话"""
        import threading
        import concurrent.futures
        
        def run_async_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(self.mcp_client.create_all_sessions())
            finally:
                new_loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async_in_thread)
            future.result(timeout=30)  # 30秒超时
            logger.info("MCP会话创建完成")
    
    def _call_mcp_node(self, state: WebUIMidSceneAgentState) -> Dict[str, Any]:
        """4. 调用MCP节点生成测试脚本"""
        self._send_node_start_notification("call_mcp", "调用MCP生成测试脚本")
        midscene_api_doc_path = os.path.join(settings.BASE_DIR, 'docs', 'midscene-yaml-api.md')
        try:
            with open(midscene_api_doc_path, 'r', encoding='utf-8') as f:
                midscene_api_content=f.read()
        except FileNotFoundError:
            logger.error(f"midscene-yaml-api文档文件未找到: {midscene_api_doc_path}")
        
        try:
            if not self.mcp_agent:
                raise RuntimeError("MCP智能体未初始化")
            
            # 构建用户需求描述
            description = state['description']
            target_url = state['url']
            
            # 构建MCP调用提示词
            mcp_prompt = f"""
请使用 Midscene MCP 完成以下任务：

目标URL: {target_url}
用户需求: {description}

最后请基于以上步骤，参考以下 Midscene YAML API 文档来生成正确的 YAML测试脚本：

{midscene_api_content}

编写要求：
1. 脚本必须符合 Midscene YAML 语法规范
2. web 配置部分必须包含 url
3. tasks 部分应覆盖完整业务流程，支持多步骤和多页面跳转
   - 主流程操作步骤（aiTap/aiHover/aiInput/aiKeyboardPress/aiScroll）
   - 数据提取（aiQuery/aiNumber/aiBoolean/aiString）
   - 验证与断言（aiAssert）
   - 合理使用 sleep 和 aiWaitFor 防止页面未加载
4. 每个关键步骤单独拆分为 task，task 内部用 flow 表达操作流
5. 对 aiQuery 提取的数据，在 aiAssert 中直接引用
6. 关键步骤失败时，可设置 continueOnError: false，确保流程可靠
7. 每个 task 可加 logScreenshot 记录操作页面，便于调试和报告

YAML 脚本示例（仅供结构参考）：
```yaml
web:
  url: https://www.baidu.com

tasks:
  - name: 登录_输入账号
    flow:
      - aiInput: 在用户名输入框中输入"test_user"
      - aiInput: 在密码输入框中输入"123456"
      - aiTap: 点击登录按钮
      - aiWaitFor: 页面加载完成并显示主页

  - name: 搜索_执行搜索
    flow:
      - aiInput: 在搜索框中输入lemon
      - aiKeyboardPress: Enter
      - sleep: 2000
      - aiWaitFor: 搜索结果页面已加载

  - name: 提取搜索结果
    flow:
      - aiQuery: >
          {{title: string, description: string, url: string}}[], 
          提取搜索结果的标题、描述和链接
        name: searchResults
      - aiNumber: "统计搜索结果总数"
      - aiString: "获取第一个搜索结果的标题"

  - name: 验证搜索结果
    flow:
      - aiAssert: searchResults.length > 0
      - aiAssert: searchResults[0].title 包含lemon
      - logScreenshot: "搜索结果截图"

  - name: 收尾_退出系统
    flow:
      - aiTap: 点击退出/登出按钮
      - aiWaitFor: 登录页面显示
      - logScreenshot: "退出页面截图"
"""
            
            # 发送开始生成的消息
            self._send_websocket_message("🚀 开始使用MCP智能体生成测试脚本...\n", "MCP智能体生成")
            self._send_websocket_message(f"📋 用户需求: {description}\n", "MCP智能体生成")
            self._send_websocket_message(f"🌐 目标URL: {target_url}\n", "MCP智能体生成")
            self._send_websocket_message("📝 MCP智能体终端输出:\n", "MCP智能体生成")
            
            # 调用MCP智能体生成脚本
            raw_output = self._call_mcp_agent(mcp_prompt)
            
            if not raw_output:
                logger.warning("MCP生成脚本失败: 返回空内容")
                # 发送失败消息
                self._send_websocket_message("❌ MCP生成脚本失败: 返回空内容\n", "MCP智能体生成")
                return {
                    "current_step": "script_generation_failed",
                    "yaml_test_script": None
                }
            
            # 从MCP输出中提取YAML脚本
            script = extract_yaml_from_output(raw_output)
            
            if not script:
                logger.warning("从MCP输出中提取YAML脚本失败")
                # 发送失败消息
                self._send_websocket_message("❌ 从MCP输出中提取YAML脚本失败\n", "MCP智能体生成")
                return {
                    "current_step": "script_generation_failed",
                    "yaml_test_script": None
                }
            
            logger.info(f"MCP生成脚本成功，提取的YAML长度: {len(script)}")
            
            # 发送成功消息和脚本内容
            self._send_websocket_message(f"\n✅ MCP智能体运行完成！脚本长度: {len(script)} 字符\n", "MCP智能体生成")
            self._send_websocket_message("📝 生成的测试脚本:\n```yaml\n", "MCP智能体生成")
            # 分段发送脚本内容，避免单次发送过长
            chunk_size = 1000
            for i in range(0, len(script), chunk_size):
                chunk = script[i:i+chunk_size]
                self._send_websocket_message(chunk, "MCP智能体生成")
            self._send_websocket_message("\n```\n\n🎉 脚本生成完成！\n", "MCP智能体生成")
            
            return {
                "yaml_test_script": script,
                "current_step": "script_generated",
                "script_review_count": 0
            }
                
        except Exception as e:
            logger.error(f"MCP生成测试脚本失败: {e}")
            # 发送错误消息
            self._send_websocket_message(f"❌ MCP生成测试脚本失败: {str(e)}\n", "MCP智能体生成")
            return {
                "current_step": "script_generation_failed",
                "yaml_test_script": None
            }
    
    def _call_mcp_agent(self, prompt: str) -> str:
        """调用MCP智能体生成脚本"""
        try:
            # 检查是否有运行的事件循环
            try:
                loop = asyncio.get_running_loop()
                # 在运行的事件循环中，使用线程池来运行异步任务
                return self._run_mcp_agent_in_thread(prompt)
            except RuntimeError:
                # 没有运行的事件循环，使用asyncio.run
                return asyncio.run(self._run_mcp_agent_async(prompt))
        except Exception as e:
            logger.error(f"MCP智能体调用失败: {e}")
            return None
    
    def _run_mcp_agent_in_thread(self, prompt: str) -> str:
        """在线程中运行MCP智能体"""
        import threading
        import concurrent.futures
        
        def run_async_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(self._run_mcp_agent_async(prompt))
            finally:
                new_loop.close()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async_in_thread)
            return future.result(timeout=120)  # 2分钟超时
    
    async def _run_mcp_agent_async(self, prompt: str) -> str:
        """异步运行MCP智能体，直接使用run方法并转发终端日志"""
        try:
            logger.info("开始运行MCP智能体")
            
            # 发送开始运行的通知
            self._send_websocket_message("🚀 开始运行MCP智能体...\n", "MCP智能体运行")
            
            # 使用MCP智能体的run方法
            result = await self.mcp_agent.run(prompt)
            
            # 发送运行完成通知
            self._send_websocket_message("✅ MCP智能体运行完成\n", "MCP智能体运行")
            
            return result
            
        except Exception as e:
            logger.error(f"运行MCP智能体失败: {e}")
            import traceback
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            
            # 发送错误信息到前端
            self._send_websocket_message(f"❌ MCP智能体运行失败: {str(e)}\n", "MCP智能体运行")
            raise
    

    
    def _decide_after_config_load(self, state: WebUIMidSceneAgentState) -> str:
        """配置加载后的决策"""
        if state.get("current_step") == "config_loaded":
            return "initialize_mcp"
        else:
            return "__end__"
    
    def _decide_after_mcp_init(self, state: WebUIMidSceneAgentState) -> str:
        """MCP初始化后的决策"""
        if state.get("current_step") == "mcp_initialized":
            return "call_mcp"
        else:
            return "__end__"
    
    def _decide_after_mcp_call(self, state: WebUIMidSceneAgentState) -> str:
        """MCP调用后的决策"""
        if state.get("current_step") == "script_generated" and state.get("yaml_test_script"):
            return "convert_yaml_to_js"
        else:
            return "__end__"
    
    def _decide_after_conversion(self, state: WebUIMidSceneAgentState) -> str:
        """转换后的决策"""
        if state.get("current_step") == "conversion_completed" and state.get("test_script"):
            return "save_script"
        else:
            return "__end__"
    
    def _convert_yaml_to_js_node(self, state: WebUIMidSceneAgentState) -> WebUIMidSceneAgentState:
        """将YAML测试脚本转换为JavaScript格式"""
        try:
            yaml_script = state.get("yaml_test_script")
            if not yaml_script:
                logger.warning("没有YAML脚本需要转换")
                return {
                    **state,
                    "current_step": "conversion_failed"
                }
            
            logger.info("开始将YAML脚本转换为JavaScript格式")
            self._send_websocket_message("🔄 开始转换YAML脚本为JavaScript格式...\n", "脚本转换")
            
            # 构建转换提示词
            conversion_prompt = self._build_yaml_to_js_conversion_prompt(yaml_script)
            
            # 使用LLM进行转换
            from .model_manager import get_llm_manager
            llm_manager = get_llm_manager()
            from langchain_core.messages import HumanMessage
            messages = [HumanMessage(content=conversion_prompt)]
            raw_converted_output = llm_manager.invoke(messages)
            
            if not raw_converted_output:
                logger.warning("YAML到JavaScript转换失败")
                self._send_websocket_message("❌ YAML到JavaScript转换失败\n", "脚本转换")
                return {
                    **state,
                    "current_step": "conversion_failed"
                }
            
            # 从LLM输出中提取JavaScript脚本
            converted_script = extract_javascript_from_output(raw_converted_output)
            
            if not converted_script:
                logger.warning("从转换输出中提取JavaScript脚本失败")
                self._send_websocket_message("❌ 提取JavaScript脚本失败\n", "脚本转换")
                return {
                    **state,
                    "current_step": "extraction_failed"
                }
            
            logger.info(f"YAML到JavaScript转换成功，转换后脚本长度: {len(converted_script)}")
            
            # 发送成功消息和转换后的脚本
            self._send_websocket_message(f"\n✅ YAML到JavaScript转换完成！脚本长度: {len(converted_script)} 字符\n", "脚本转换")
            self._send_websocket_message("📝 转换后的JavaScript测试脚本:\n```javascript\n", "脚本转换")
            
            # 分段发送脚本内容
            chunk_size = 1000
            for i in range(0, len(converted_script), chunk_size):
                chunk = converted_script[i:i+chunk_size]
                self._send_websocket_message(chunk, "脚本转换")
            self._send_websocket_message("\n```\n\n🎉 脚本转换完成！\n", "脚本转换")
            
            return {
                **state,
                "yaml_test_script": yaml_script,  # 保留原始YAML脚本
                "test_script": converted_script,  # 添加转换后的JavaScript脚本
                "current_step": "conversion_completed"
            }
            
        except Exception as e:
            logger.error(f"YAML到JavaScript转换失败: {e}")
            self._send_websocket_message(f"❌ YAML到JavaScript转换失败: {str(e)}\n", "脚本转换")
            return {
                **state,
                "current_step": "conversion_failed"
            }
    
    def _build_yaml_to_js_conversion_prompt(self, yaml_script: str) -> str:
        """构建YAML到JavaScript转换的提示词"""
        # 读取JavaScript API文档
        js_api_doc = self._read_js_api_documentation()
        
        prompt = f"""你是一个专业的测试脚本转换专家。请将以下YAML格式的MidScene测试脚本转换为JavaScript格式的测试脚本。

## JavaScript API文档参考

{js_api_doc}

## 转换要求

1. 使用Playwright和MidScene的JavaScript API
2. 保持原有的测试逻辑和流程
3. 使用正确的JavaScript语法和ES6+特性
4. 包含必要的导入语句和初始化代码
5. 确保脚本可以直接运行

## 原始YAML脚本

```yaml
{yaml_script}
```

## 请生成对应的JavaScript测试脚本

要求：
- 使用 `import {{ chromium }} from 'playwright'`
- 使用 `import {{ PlaywrightAgent }} from '@midscene/web/playwright'`
- 使用 `import 'dotenv/config'`
- 包含完整的浏览器启动和页面设置代码
- 将YAML中的任务转换为相应的JavaScript API调用
- 保持原有的测试步骤和断言

请直接输出JavaScript代码，不要包含任何解释文字："""

        return prompt
    
    def _read_js_api_documentation(self) -> str:
        """读取JavaScript API文档"""
        try:
            doc_path = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'midscene-js-api.md')
            if os.path.exists(doc_path):
                with open(doc_path, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
                logger.warning(f"JavaScript API文档不存在: {doc_path}")
                return "JavaScript API文档未找到，请参考MidScene官方文档。"
        except Exception as e:
            logger.error(f"读取JavaScript API文档失败: {e}")
            return "JavaScript API文档读取失败，请参考MidScene官方文档。"
    
    
    def _save_script_node(self, state: WebUIMidSceneAgentState) -> WebUIMidSceneAgentState:
        """保存脚本到数据库"""
        try:
            yaml_script = state.get("yaml_test_script")
            js_script = state.get("test_script")
            user_id = state.get("user_id")
            project_id = state.get("project_id")
            script_name = state.get("script_name", "WebUI测试脚本")
            description = state.get("description")
            url = state.get("url")
            
            if not yaml_script or not js_script:
                logger.warning("没有脚本内容需要保存")
                return {
                    **state,
                    "current_step": "save_failed"
                }
            
            logger.info("开始保存脚本到数据库")
            self._send_websocket_message("💾 开始保存脚本到数据库...\n", "脚本保存")
            
            # 导入模型
            from web_testing.models import WebUITestCase
            from projects.models import Project
            from django.contrib.auth import get_user_model
            
            # 获取自定义用户模型
            User = get_user_model()
            
            # 获取用户和项目
            user = User.objects.get(id=user_id)
            project = Project.objects.get(id=project_id) if project_id else None
            
            # 生成文件路径
            import os
            from django.conf import settings
            import uuid
            
            script_id = str(uuid.uuid4())
            yaml_file_path = os.path.join(settings.MEDIA_ROOT, 'scripts', f'{script_id}.yaml')
            js_file_path = os.path.join(settings.MEDIA_ROOT, 'scripts', f'{script_id}.js')
            
            # 确保目录存在
            os.makedirs(os.path.dirname(yaml_file_path), exist_ok=True)
            
            # 保存文件
            with open(yaml_file_path, 'w', encoding='utf-8') as f:
                f.write(yaml_script)
            
            with open(js_file_path, 'w', encoding='utf-8') as f:
                f.write(js_script)
            
            # 创建数据库记录
            test_case = WebUITestCase.objects.create(
                title=script_name,
                description=description,
                url=url,
                user=user,
                project=project,
                # MidScene 的 JavaScript 文件是独立产物，不写入 Python Playwright
                # 用例字段，避免破坏 WebUITestCase 的脚本契约。
                test_script_content=None,
                priority='medium',
                category='functional',
                preconditions=[],
                steps=[],
                expected_result='脚本执行成功'
            )
            from web_testing.script_contract import store_script_content
            store_script_content(
                test_case,
                None,
                source='manual',
                generation_metadata={
                    'midscene_yaml_path': yaml_file_path,
                    'midscene_js_path': js_file_path,
                },
            )
            
            logger.info(f"测试用例保存成功，ID: {test_case.id}")
            self._send_websocket_message(f"✅ 测试用例保存成功！测试用例ID: {test_case.id}\n", "脚本保存")
            self._send_websocket_message(f"📁 YAML文件: {yaml_file_path}\n", "脚本保存")
            self._send_websocket_message(f"📁 JavaScript文件: {js_file_path}\n", "脚本保存")
            
            return {
                **state,
                "script_id": test_case.id,
                "current_step": "saved"
            }
            
        except Exception as e:
            logger.error(f"保存脚本失败: {e}")
            self._send_websocket_message(f"❌ 保存脚本失败: {str(e)}\n", "脚本保存")
            return {
                **state,
                "current_step": "save_failed"
            }
    
    
    
    
    
    async def run(self, description: str, url: str = "") -> Dict[str, Any]:
        """运行WebUI测试脚本生成智能体"""
        try:
            # 强制使用LangGraph工作流
            if not self.workflow:
                raise RuntimeError("LangGraph工作流未初始化，无法运行WebUI测试脚本生成智能体")
            
            return await self._run_with_langgraph(description, url)
                
        except Exception as e:
            error_msg = f"运行WebUI测试脚本生成智能体失败: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "current_step": "failed"
            }
        finally:
            # 清理MCP客户端
            if self.mcp_client:
                await self.mcp_client.close_all_sessions()
    
    async def _run_with_langgraph(self, description: str, url: str) -> Dict[str, Any]:
        """使用LangGraph工作流运行"""
        # 初始化状态
        initial_state = {
            "description": description,
            "url": url,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "script_name": self.script_name,
            "mcp_config": self.mcp_config,
            "yaml_test_script": None,
            "test_script": None,
            "script_id": None,
            "current_step": "initialized"
        }
        
        # 运行工作流
        result = await self.workflow.ainvoke(initial_state)
        
        # 检查是否有错误
        if not result.get("test_script"):
            return {
                "success": False,
                "error": "测试脚本生成失败",
                "current_step": result.get("current_step", "unknown")
            }
        
        # 返回成功结果
        return {
            "success": True,
            "test_script": result.get("test_script"),
            "yaml_test_script": result.get("yaml_test_script"),
            "script_id": result.get("script_id"),
            "model_info": self.vision_manager.get_model_info(),
            "model_type": "vision",
            "current_step": result.get("current_step", "completed")
        }




def create_webui_midscene_agent(user, user_id: int = None, enable_streaming: bool = True) -> WebUIMidSceneAgent:
    """创建WebUI MidScene智能体实例"""
    return WebUIMidSceneAgent(user, user_id, enable_streaming)
