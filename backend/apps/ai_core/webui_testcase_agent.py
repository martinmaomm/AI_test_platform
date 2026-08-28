"""
Web UI测试用例生成智能体系统
采用角色化分工设计，多个子智能体协作生成测试用例
使用LangGraph进行智能体编排，支持流式输出
"""
import json
import logging
from typing import Dict, List, Any, Optional, TypedDict, Callable, Union
from django.utils import timezone
from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .model_manager import get_llm_manager
from .rag_service import get_rag_manager
from web_testing.models import WebUITestCase, WebUITestModule
from web_testing.constants import WEBUI_STEPS_MAPPING
from common.websocket import send_node_start_notification_helper, websocket_message_service
from common.parsers import extract_json_from_output, fix_json_format, parse_json_robust
from datetime import datetime

logger = logging.getLogger(__name__)


# Pydantic 模型定义 - 用于结构化输出
class TestStep(BaseModel):
    """测试步骤"""
    step_id: int = Field(description="步骤编号")
    action: str = Field(description="操作动作")
    target: str = Field(description="目标元素")
    value: Optional[str] = Field(default=None, description="输入值或参数")
    description: str = Field(description="步骤描述")


class TestCase(BaseModel):
    """单个测试用例"""
    id: str = Field(description="测试用例ID")
    title: str = Field(description="测试用例标题")
    description: str = Field(description="测试用例描述")
    priority: str = Field(description="优先级：high/medium/low")
    category: str = Field(description="类别：functional/negative/boundary")
    module_id: Optional[int] = Field(default=None, description="匹配的业务模块ID")
    preconditions: List[str] = Field(default_factory=list, description="前置条件列表")
    steps: List[TestStep] = Field(default_factory=list, description="测试步骤列表")
    expected_result: str = Field(description="预期结果")


class CoverageAnalysis(BaseModel):
    """覆盖率分析"""
    normal_cases: int = Field(default=0, description="正常用例数量")
    exception_cases: int = Field(default=0, description="异常用例数量")
    boundary_cases: int = Field(default=0, description="边界用例数量")


class ReviewedTestCasesResponse(BaseModel):
    """审核后的测试用例响应结构"""
    test_cases: List[TestCase] = Field(default_factory=list, description="测试用例列表")
    coverage_analysis: CoverageAnalysis = Field(description="覆盖率分析")


# 定义WebUI测试用例生成Agent状态数据结构
class WebUITestCaseGeneratorState(TypedDict):
    """WebUI测试用例生成Agent的状态数据"""
    user_input: str                    # 用户需求输入
    project_id: Optional[int]          # 项目ID
    module_id: Optional[int]           # 用户选择的模块ID（用于提取UI资产）
    user_id: int                       # 用户ID
    test_case_name: Optional[str]      # 测试用例名称
    parsed_requirements: Optional[Dict[str, Any]]  # 解析后的需求
    designed_test_cases: Optional[Dict[str, Any]]  # 设计的测试用例
    formatted_test_cases: Optional[Dict[str, Any]] # 格式化后的测试用例
    reviewed_test_cases: Optional[Dict[str, Any]]  # 审核后的测试用例（结构化输出）
    test_case_id: Optional[int]        # 保存的测试用例ID（已弃用，使用created_test_cases）
    created_test_cases: Optional[List[Dict[str, Any]]]  # 创建的测试用例列表
    test_cases_count: Optional[int]    # 创建的测试用例数量
    current_step: str                  # 当前执行步骤
    execution_timeline: List[Dict[str, Any]]  # 执行时间线
    error_message: Optional[str]       # 错误信息


class WebUITestCaseGenerator:
    """Web UI测试用例生成器主类"""
    
    def __init__(self, user_id: int, user=None, enable_streaming: bool = True, 
                 streaming_callback: Optional[Callable[[str, str], None]] = None):
        self.user = user
        self.user_id = user_id
        self.enable_streaming = enable_streaming
        self.streaming_callback = streaming_callback
        self.project_id = None
        self.test_case_name = None
        
        # LLM和RAG管理器延迟初始化（在需求解析节点中初始化）
        self.llm_manager = None
        self.rag_manager = None
        
        # 构建LangGraph工作流
        self.workflow = self._build_workflow()
    
    def _build_workflow(self) -> StateGraph:
        """构建LangGraph工作流"""
        # 创建状态图
        graph = StateGraph(WebUITestCaseGeneratorState)
        
        # 添加所有节点
        graph.add_node("requirement_parsing", self._requirement_parsing_node)
        graph.add_node("test_designing", self._test_designing_node)
        graph.add_node("test_reviewing", self._test_reviewing_node)
        graph.add_node("save_to_database", self._save_to_database_node)
        
        # 设置线性执行流程
        graph.set_entry_point("requirement_parsing")
        graph.add_edge("requirement_parsing", "test_designing")
        graph.add_edge("test_designing", "test_reviewing")
        graph.add_edge("test_reviewing", "save_to_database")
        graph.add_edge("save_to_database", END)
        
        return graph.compile()
    
    def _send_node_start_notification(self, node_name: str, node_display_name: str, node_description: Optional[str] = None):
        """发送节点开始执行通知（使用统一的辅助函数）"""
        return send_node_start_notification_helper(
            user_id=self.user_id,
            node_name=node_name,
            node_display_name=node_display_name,
            node_description=node_description,
            enable_streaming=self.enable_streaming,
            room_type="webui_test_generation"
        )
    
    def _send_streaming_output(self, content: str, step: str = "test_reviewing") -> bool:
        """发送流式输出消息的辅助方法"""
        if not self.enable_streaming or not self.user_id:
            return False
        
        try:
            timestamp = datetime.now().isoformat()
            success = websocket_message_service.send_streaming_output(
                user_id=self.user_id,
                step=step,
                content=content,
                timestamp=timestamp,
                room_type="webui_test_generation"
            )
            if not success:
                logger.warning(f"WebSocket流式消息发送失败: step={step}")
            return success
        except Exception as e:
            logger.warning(f"发送流式输出消息失败: {e}")
            return False
    
    def _stream_llm_response(self, messages: List, step_name: str) -> str:
        """流式调用LLM并处理响应（使用llm_manager的统一方法）"""
        # 确保LLM管理器已初始化
        if self.llm_manager is None:
            self.llm_manager = get_llm_manager()
        return self.llm_manager.stream_invoke_with_websocket(
            messages=messages,
            step_name=step_name,
            enable_streaming=self.enable_streaming,
            user_id=self.user_id,
            streaming_callback=self.streaming_callback,
            room_type="webui_test_generation"
        )
    
    def run(self, user_input: str, project_id: Optional[int] = None, module_id: Optional[int] = None) -> Dict[str, Any]:
        """
        运行Web UI测试用例生成器

        Args:
            user_input: 用户的一句话需求
            project_id: 项目ID（可选，用于RAG检索）
            module_id: 用户选择的模块ID（可选，用于提取UI资产）

        Returns:
            生成的测试用例结果
        """
        try:
            logger.info(f"开始运行Web UI测试用例生成器，用户输入: {user_input}")

            # 设置项目ID
            self.project_id = project_id

            return self._run_with_langgraph(user_input, project_id, module_id)
                
        except Exception as e:
            error_msg = f"运行Web UI测试用例生成器失败: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "current_step": "failed"
            }
    
    def _run_with_langgraph(self, user_input: str, project_id: Optional[int] = None, module_id: Optional[int] = None) -> Dict[str, Any]:
        """使用LangGraph工作流运行"""
        # 初始化状态
        initial_state = WebUITestCaseGeneratorState(
            user_input=user_input,
            project_id=project_id,
            module_id=module_id,
            user_id=self.user_id,
            test_case_name=None,
            parsed_requirements=None,
            designed_test_cases=None,
            formatted_test_cases=None,
            reviewed_test_cases=None,
            test_case_id=None,
            created_test_cases=None,
            test_cases_count=None,
            current_step="initialized",
            execution_timeline=[],
            error_message=None
        )
        
        # 运行工作流
        result = self.workflow.invoke(initial_state)
        
        # 检查是否有错误
        if result.get("error_message"):
            return {
                "success": False,
                "error": result["error_message"],
                "current_step": result.get("current_step", "unknown"),
                "execution_timeline": result.get("execution_timeline", [])
            }
        
        # 返回成功结果
        return {
            "success": True,
            "user_input": user_input,
            "project_id": project_id,
            "generated_at": timezone.now().isoformat(),
            "execution_timeline": result.get("execution_timeline", []),
            "test_cases": result.get("reviewed_test_cases") or result.get("formatted_test_cases", {}),
            "created_test_cases": result.get("created_test_cases", []),
            "test_cases_count": result.get("test_cases_count", 0),
            "test_case_id": result.get("test_case_id"),  # 保持向后兼容
            "message": f'Web UI测试用例生成成功，共创建 {result.get("test_cases_count", 0)} 个测试用例'
        }
    
    def _requirement_parsing_node(self, state: WebUITestCaseGeneratorState) -> WebUITestCaseGeneratorState:
        """需求解析节点 - 通过RAG获取业务上下文"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过需求解析，因为前一个节点有错误")
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("requirement_parsing", "需求解析", "通过RAG检索项目知识库，获取业务上下文信息")
            
            # 初始化LLM和RAG管理器（在节点开始通知之后）
            if self.llm_manager is None:
                self.llm_manager = get_llm_manager()
            if self.rag_manager is None:
                self.rag_manager = get_rag_manager()
            
            state["current_step"] = "parsing_requirements"
            
            user_input = state.get("user_input")
            project_id = state.get("project_id")
            
            business_context = []
            if project_id:
                try:
                    search_result = self.rag_manager.search_documents(
                        query=user_input,
                        top_k=5,
                        filter_metadata={"project_id": project_id}
                    )
                    if search_result.get('success') and search_result.get('results'):
                        business_context = search_result['results']
                except Exception as e:
                    logger.warning(f"RAG检索失败: {e}")

            # 提取前端传递的模块 ID，并查询结构化 UI 资产
            module_id = state.get("module_id")
            ui_assets_context = ""
            if module_id:
                try:
                    from web_testing.models import WebPage

                    pages = WebPage.objects.filter(module_id=module_id).prefetch_related('elements')
                    if pages.exists():
                        ui_assets_context = "【本模块可用的页面与元素资产库】\n"
                        for page in pages:
                            ui_assets_context += f"- 页面: {page.name} ({page.page_class_name or ''})\n"
                            elements = page.elements.all()
                            if elements:
                                ui_assets_context += "  包含元素: " + ", ".join([f"'{el.name}'" for el in elements]) + "\n"
                except Exception as e:
                    logger.warning(f"获取模块UI资产失败: {e}")

            state["parsed_requirements"] = {
                "user_input": user_input,
                "business_context": business_context,
                "ui_assets_context": ui_assets_context
            }
            logger.info("需求解析完成")
            
            return state
        except Exception as e:
            error_msg = f"需求解析失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
            return state
    
    def _test_designing_node(self, state: WebUITestCaseGeneratorState) -> WebUITestCaseGeneratorState:
        """测试用例设计节点 - 流式调用并自动解析保存"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过测试用例设计，因为前一个节点有错误")
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("test_designing", "用例设计", "基于用户需求和业务上下文，设计全面的测试用例，包括正常路径、异常输入和边界情况")
            
            state["current_step"] = "designing_test_cases"
            
            parsed_requirements = state.get("parsed_requirements", {})
            project_id = state.get("project_id")

            # 获取业务模块列表，用于用例自动挂载
            modules_context = "（暂无已配置的模块）"
            if project_id:
                try:
                    modules = WebUITestModule.objects.filter(project_id=project_id).order_by('order', 'id').values_list('id', 'name')
                    if modules:
                        modules_context = ", ".join(f"{mid}: {name}" for mid, name in modules)
                except Exception as e:
                    logger.warning(f"查询业务模块失败: {e}")

            # 动态提取标准动作资产及参数约束规则
            allowed_actions_text = ""
            for action_key, config in WEBUI_STEPS_MAPPING.items():
                desc = config.get("description", config.get("label", action_key))
                need_target = config.get("needTarget", True)
                need_value = config.get("needValue", False)

                target_rule = "【必填】" if need_target else "【必须为空字符串】"
                value_rule = "【必填】" if need_value else "【必须为空字符串】"

                allowed_actions_text += f"- `{action_key}` ({desc}) -> target参数: {target_rule}, value参数: {value_rule}\n"

            # 查看RAG检索结果
            logger.info("================ RAG 检索结果大揭秘 ================")
            logger.info(f"喂给大模型的解析后需求内容是: \n{state.get('parsed_requirements')}")
            logger.info("====================================================")
            # 构建Prompt
            prompt = ChatPromptTemplate.from_template("""
            你是一名严谨的高级测试工程师，专门负责基于明确的需求文档设计结构化测试用例。

            【业务模块参考】
            当前系统已有的模块列表如下（格式为 ID: 模块名）：
            {modules_context}
            请根据用例的语义，从上述列表中选择最匹配的 ID 填入 `module_id`。如果不匹配任何模块，请填 null。

            你的职责与绝对红线：
            1. 【忠于需求】严格基于《解析后的需求》提取输入字段、业务流和规则约束（如长度、格式要求）。
            2. 【严禁发散】测试步骤和输入参数必须 100% 贴合需求描述。如果需求中只规定了手机号、验证码、用户名等字段，**绝对不允许**生成包含“邮箱”、“身份证”、“人脸识别”等未提及字段的测试用例。
            3. 【场景覆盖】在现有需求输入项的边界内，设计正常路径、异常输入（如特殊字符、格式错误）、边界情况（如长度超限）。
            4. 【精简有效】最多只生成 6 到 8 个最具代表性的核心测试用例，保证质量。
            5. 【标准动作强约束】你的 steps 中的 action 必须且只能使用下面列表中的合法代码，绝不允许自己发明！同时必须严格遵守 target 和 value 的传参规则：
            {allowed_actions_text}
            特别注意：
            - 如果 action 是 goto，target 必须留空，URL 填写在 value 中。
            - target 的值应该是一个能准确描述页面元素的自然语言（如 "登录按钮", "手机号输入框"），以便后续映射。
            6. 【UI 元素严格绑定 (Target约束)】：《解析后的需求》中如果包含了【本模块可用的页面与元素资产库】，你在设计 steps 的 `target` 字段时，**必须且只能**从中挑选已存在的元素名称（例如原封不动地填入 "提交按钮" 或 "手机号输入框"）。
                - 绝对禁止捏造、猜测不存在的元素名称！
                - 如果在资产库中找不到完全对应的元素，请选择语义最接近的现有元素。
                - 仅当动作是 goto 时，target 才可以为空。

            【最高排版指令】：
            为了兼顾用户阅读体验和系统自动解析，你的输出必须严格包含以下两个部分，且顺序不可颠倒：
            第一部分：Markdown 表格概览
            ⚠️ 核心提速要求：为了提高生成速度，你的 Markdown 表格中【仅展示】：用例编号、用例名称、测试类型、预期结果这 4 列。
            绝对禁止在表格中输出"操作步骤"！将详细的操作步骤全部保留到第二部分的 JSON 数据中。
            第二部分：严格 JSON 数据
            在表格完全输出完毕后，请使用严格的 JSON 格式输出完整的数据，必须包裹在 json 和```之间。你必须且只能输出一个完整的 JSON 对象，包含所有的测试用例（在 JSON 中必须包含完整的 steps 步骤）。

            解析后的需求: 
            ---
            {parsed_requirements}
            ---


            输出格式要求：
            {{
            "test_cases": [
                {{
                "id": "TC001",
                "title": "测试用例标题",
                "module_id": 1, 
                "description": "测试用例描述",
                "priority": "high/medium/low",
                "category": "functional/negative/boundary",
                "preconditions": ["前置条件1", "前置条件2"],
                "steps": [
                    {{
                    "step_id": 1,
                    "action": "操作动作",
                    "target": "目标元素",
                    "value": "输入值或参数",
                    "description": "步骤描述"
                    }}
                ],
                "expected_result": "预期结果"
                }}
            ],
            "coverage_analysis": {{
                    "normal_cases": 3,
                    "exception_cases": 2,
                    "boundary_cases": 2
            }}
            }}

            请确保测试用例设计严谨、可执行。只返回JSON，不要其他内容。
            """)
            
            # 流式调用LLM（实时推送原始文本到WebSocket）
            messages = prompt.format_messages(
                modules_context=modules_context,
                allowed_actions_text=allowed_actions_text,
                parsed_requirements=json.dumps(parsed_requirements, ensure_ascii=False)
            )
            
            response = self._stream_llm_response(messages, "test_designing")
            
            # 流式结束后，直接将原始响应传递给审核节点
            state["designed_test_cases"] = {"response_text": response}
            
            return state
            
        except Exception as e:
            error_msg = f"测试用例设计失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
            return state
    
    def _test_reviewing_node(self, state: WebUITestCaseGeneratorState) -> WebUITestCaseGeneratorState:
        """用例审核节点 - 使用Pydantic结构化输出审核和规范化测试用例"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过用例审核，因为前一个节点有错误")
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("test_reviewing", "用例审核", "使用Pydantic结构化输出对测试用例进行审核和规范化，确保测试用例完整、准确、可执行")
            
            # 发送流式输出消息，提示正在进行结构化输出
            self._send_streaming_output("正在进行结构化输出，使用Pydantic模型审核和规范化测试用例...", "test_reviewing")
            
            state["current_step"] = "reviewing_test_cases"
            
            # 获取用例设计节点的原始响应
            designed_test_cases = state.get("designed_test_cases", {})
            response_text = designed_test_cases.get("response_text", "") if isinstance(designed_test_cases, dict) else ""
            
            if not response_text:
                logger.warning("用例设计节点未生成响应，跳过审核")
                state["reviewed_test_cases"] = self._get_default_formatted_test_cases()
                return state
            
            # 确保LLM管理器已初始化
            if self.llm_manager is None:
                self.llm_manager = get_llm_manager()
            
            # 检查LLM是否可用
            if not self.llm_manager or not self.llm_manager.current_llm:
                error_msg = "LLM不可用，无法进行结构化输出审核"
                logger.error(error_msg)
                state["error_message"] = error_msg
                state["reviewed_test_cases"] = self._get_default_formatted_test_cases()
                
                # 发送LLM不可用消息
                error_message = f"❌ {error_msg}\n\n用例审核节点执行失败。"
                self._send_streaming_output(error_message, "test_reviewing")
                
                return state
            
            # 获取业务模块列表，与设计节点保持一致，避免审核阶段迷失
            modules_context = "无"
            if state.get("project_id"):
                try:
                    modules = WebUITestModule.objects.filter(project_id=state["project_id"]).values('id', 'name')
                    if modules.exists():
                        modules_context = "\n".join([f"{m['id']}: {m['name']}" for m in modules])
                except Exception as e:
                    logger.warning(f"审核节点查询业务模块失败: {e}")

            # 构建审核Prompt，直接使用原始响应文本
            prompt = ChatPromptTemplate.from_template("""
            你是一名资深的测试用例审核专家，负责对已设计的测试用例进行审核和规范化。

            【业务模块参考】
            {modules_context}

            你的职责：
            1. 从以下文本中提取测试用例信息
            2. 审核测试用例的完整性和准确性
            3. 确保测试步骤清晰、可执行
            4. 验证测试用例覆盖了正常路径、异常情况和边界条件
            5. 规范化测试用例的格式和结构
            6. 补充缺失的信息，优化测试步骤描述

            原始需求: {user_input}

            用例设计节点生成的原始响应:
            {designed_test_cases_response}

            请从上述响应中提取测试用例信息，并进行审核和规范化，确保：
            - 每个测试用例都有完整的步骤描述
            - 步骤顺序合理，逻辑清晰
            - 预期结果明确、可验证
            - 测试用例覆盖全面

            请返回审核和规范化后的测试用例，使用标准的结构化格式。
            """)
            
            # 准备消息
            messages = prompt.format_messages(
                modules_context=modules_context,
                user_input=state.get("user_input", ""),
                designed_test_cases_response=response_text
            )
            
            # 使用结构化输出获取Pydantic模型，建立多层防御与降级机制
            try:
                structured_llm = self.llm_manager.current_llm.with_structured_output(ReviewedTestCasesResponse)
                response = structured_llm.invoke(messages)

                # 1. 强力判空拦截：response 为 None 时严禁调用 model_dump()
                if response is not None:
                    reviewed_data = response.model_dump()
                else:
                    # 2. 降级解析（Fallback）：从原始文本中提取 JSON
                    logger.warning("结构化输出返回 None，尝试手动解析原始文本...")
                    raw_json = extract_json_from_output(response_text)
                    if raw_json:
                        try:
                            reviewed_data = parse_json_robust(raw_json)
                        except ValueError:
                            raw_json = fix_json_format(raw_json)
                            reviewed_data = parse_json_robust(raw_json)
                    else:
                        raise ValueError("无法从原始响应中提取有效 JSON 数据")

                # 转换为与 formatted_test_cases 兼容的格式
                reviewed_test_cases = self._build_reviewed_test_cases(reviewed_data)
                state["reviewed_test_cases"] = reviewed_test_cases
                logger.info(f"用例审核完成，共审核 {len(reviewed_test_cases.get('test_cases', []))} 个测试用例")

                # 发送审核完成消息
                statistics = reviewed_test_cases.get('statistics', {})
                result_message = (
                    f"✅ 用例审核完成！\n\n"
                    f"📊 审核统计：\n"
                    f"  • 总用例数：{statistics.get('total_cases', 0)}\n"
                    f"  • 功能用例：{statistics.get('functional_cases', 0)}\n"
                    f"  • 异常用例：{statistics.get('negative_cases', 0)}\n"
                    f"  • 边界用例：{statistics.get('boundary_cases', 0)}\n\n"
                    f"所有测试用例已通过审核和规范化。"
                )
                self._send_streaming_output(result_message, "test_reviewing")

            except Exception as e:
                # 3. 最终兜底：记录错误并发送 WebSocket 通知，不让系统抛出 500
                logger.error(f"用例格式化失败: {e}", exc_info=True)
                # 统一 WebSocket 错误提示：清晰易懂，避免原始堆栈泄露
                if isinstance(e, json.JSONDecodeError):
                    user_msg = "用例格式化失败: 无法解析 AI 返回的 JSON 格式"
                else:
                    user_msg = f"用例格式化失败: {str(e)}"
                state["error_message"] = user_msg
                state["reviewed_test_cases"] = self._get_default_formatted_test_cases()
                self._send_streaming_output(f"❌ 审核节点异常: {user_msg}", "test_reviewing")

            return state
            
        except Exception as e:
            error_msg = f"用例审核失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
            state["reviewed_test_cases"] = self._get_default_formatted_test_cases()
            
            # 发送审核失败消息
            error_message = f"❌ 用例审核失败: {error_msg}\n\n已使用默认测试用例继续流程。"
            self._send_streaming_output(error_message, "test_reviewing")
            
            return state
    
    def _build_reviewed_test_cases(self, reviewed_data: Dict[str, Any]) -> Dict[str, Any]:
        """将 model_dump 或 json.loads 结果转换为统一的 reviewed_test_cases 格式"""
        test_cases = reviewed_data.get('test_cases') or reviewed_data.get('optimized_test_cases', [])
        if not isinstance(test_cases, list):
            test_cases = []
        reviewed_test_cases = {
            'test_cases': test_cases,
            'statistics': {
                'total_cases': len(test_cases),
                'functional_cases': sum(1 for tc in test_cases if tc.get('category') == 'functional'),
                'negative_cases': sum(1 for tc in test_cases if tc.get('category') == 'negative'),
                'boundary_cases': sum(1 for tc in test_cases if tc.get('category') == 'boundary')
            },
            'coverage_analysis': reviewed_data.get('coverage_analysis', {})
        }
        for test_case in reviewed_test_cases.get('test_cases', []):
            test_case.setdefault('metadata', {
                "created_by": "AI Agent",
                "automation_level": "high",
                "reviewed": True
            })
        return reviewed_test_cases

    def _parse_and_format_test_cases(self, response_text: str) -> Dict[str, Any]:
        """从流式响应中解析并格式化测试用例"""
        try:
            if not response_text:
                logger.error("响应文本为空")
                return self._get_default_formatted_test_cases()
            
            # 提取JSON内容
            json_text = extract_json_from_output(response_text)
            
            if not json_text:
                logger.error("无法从响应中提取JSON")
                return self._get_default_formatted_test_cases()
            
            # 解析JSON（使用容错解析器，支持 json-repair）
            try:
                json_data = parse_json_robust(json_text)
            except ValueError as e:
                logger.warning(f"JSON解析失败，尝试修复: {e}")
                json_text = fix_json_format(json_text)
                try:
                    json_data = parse_json_robust(json_text)
                except ValueError as e2:
                    logger.error(f"JSON修复后仍解析失败: {e2}")
                    return self._get_default_formatted_test_cases()
            
            # 转换为格式化输出格式
            formatted_test_cases = self._convert_json_to_formatted_output(json_data)
            
            # 确保每个测试用例都有metadata
            for test_case in formatted_test_cases.get('test_cases', []):
                test_case.setdefault('metadata', {
                    "created_by": "AI Agent",
                    "automation_level": "high"
                })
            
            return formatted_test_cases
            
        except Exception as e:
            logger.error(f"解析和格式化测试用例失败: {e}", exc_info=True)
            return self._get_default_formatted_test_cases()
    
    def _convert_json_to_formatted_output(self, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """将JSON数据转换为格式化输出格式"""
        try:
            # 如果已经是正确的格式，直接返回
            if 'test_cases' in json_data and 'statistics' in json_data:
                return json_data
            
            # 尝试从不同的字段中提取测试用例
            test_cases = json_data.get('test_cases', [])
            if not test_cases:
                # 尝试其他可能的字段名
                test_cases = json_data.get('optimized_test_cases', [])
            
            # 计算统计信息
            statistics = json_data.get('statistics', {})
            if not statistics:
                functional_cases = sum(1 for tc in test_cases if tc.get('category') == 'functional')
                negative_cases = sum(1 for tc in test_cases if tc.get('category') == 'negative')
                boundary_cases = sum(1 for tc in test_cases if tc.get('category') == 'boundary')
                statistics = {
                    'total_cases': len(test_cases),
                    'functional_cases': functional_cases,
                    'negative_cases': negative_cases,
                    'boundary_cases': boundary_cases
                }
            
            return {
                'test_cases': test_cases,
                'statistics': statistics
            }
        except Exception as e:
            logger.error(f"转换JSON到格式化输出失败: {e}")
            return self._get_default_formatted_test_cases()
    
    def _get_default_formatted_test_cases(self) -> Dict[str, Any]:
        """获取默认格式的测试用例（错误处理）"""
        return {
            "test_cases": [],
            "statistics": {
                "total_cases": 0,
                "functional_cases": 0,
                "negative_cases": 0,
                "boundary_cases": 0
            }
        }
    
    def _save_to_database_node(self, state: WebUITestCaseGeneratorState) -> WebUITestCaseGeneratorState:
        """保存到数据库节点 - 将每个测试用例单独保存"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过保存到数据库，因为前一个节点有错误")
            return state
            
        try:
            self._send_node_start_notification("save_to_database", "保存到数据库", "将审核后的测试用例保存到数据库，每个测试用例单独存储")
            state["current_step"] = "saving_to_database"
            
            # 使用审核后的测试用例（审核节点会通过结构化输出或JSON解析生成）
            reviewed_test_cases = state.get("reviewed_test_cases")
            
            if not reviewed_test_cases:
                raise ValueError("审核节点未生成测试用例数据")
            
            test_cases_data = reviewed_test_cases
            user_id = state.get("user_id")
            project_id = state.get("project_id")
            
            test_cases = test_cases_data.get('test_cases', [])
            
            if not test_cases:
                raise ValueError("测试用例列表为空")
            
            created_test_cases = []
            timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
            valid_module_ids = set()
            if project_id:
                valid_module_ids = set(
                    WebUITestModule.objects.filter(project_id=project_id).values_list('id', flat=True)
                )

            for i, test_case_data in enumerate(test_cases, 1):
                # 类型安全兜底：module_id 支持 int/str/null，统一转为合法 int 或 None
                raw_module_id = test_case_data.get("module_id")
                safe_module_id = None
                if raw_module_id not in [None, "", "null"]:
                    try:
                        safe_module_id = int(raw_module_id)
                    except (TypeError, ValueError):
                        pass
                if safe_module_id is not None and safe_module_id not in valid_module_ids:
                    safe_module_id = None

                test_case = WebUITestCase.objects.create(
                    title=test_case_data.get('title', f'测试用例_{timestamp}_{i:03d}'),
                    description=test_case_data.get('description', 'AI生成的Web UI测试用例'),
                    url=test_case_data.get('url', ''),
                    priority=test_case_data.get('priority', 'medium'),
                    category=test_case_data.get('category', 'functional'),
                    preconditions=test_case_data.get('preconditions', []),
                    steps=test_case_data.get('steps', []),
                    expected_result=test_case_data.get('expected_result', ''),
                    user_id=user_id,
                    project_id=project_id,
                    module_id=safe_module_id,
                    test_script_content=None
                )
                from web_testing.script_contract import store_script_content
                store_script_content(test_case, None, source='requirement_ai')
                created_test_cases.append({
                    'id': test_case.id,
                    'title': test_case.title,
                    'description': test_case.description,
                    'priority': test_case.priority,
                    'category': test_case.category,
                    'module_id': safe_module_id
                })
            
            state["created_test_cases"] = created_test_cases
            state["test_cases_count"] = len(created_test_cases)
            logger.info(f"成功保存 {len(created_test_cases)} 个测试用例到数据库")
            
            return state
            
        except Exception as e:
            error_msg = f"保存到数据库失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
            return state


# 便捷函数
def create_webui_test_case_generator(user_id: int, user=None, enable_streaming: bool = True, 
                                    streaming_callback: Optional[Callable[[str, str], None]] = None) -> WebUITestCaseGenerator:
    """创建Web UI测试用例生成器实例"""
    return WebUITestCaseGenerator(user_id, user, enable_streaming, streaming_callback)
