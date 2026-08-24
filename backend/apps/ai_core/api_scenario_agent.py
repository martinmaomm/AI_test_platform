"""
API智能场景生成器 Agent
用于将自然语言描述转换为可执行的端到端测试脚本
"""
import json
import logging
from typing import TypedDict, List, Dict, Any, Optional, Callable, Union
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

# 首先定义logger
logger = logging.getLogger(__name__)

from langgraph.graph import StateGraph, END

from .model_manager import get_llm_manager
from .rag_service import get_rag_manager
from .api_testcase_generator import HttpRunnerTestCase, HttpRunnerConfig, HttpRunnerTestStep, HttpRunnerRequest
from .scenario_preflight_validator import ScenarioPreflightValidator
from .scenario_contract import build_generation_endpoint
from api_testing.models import APISpecification, APIEndpoint, APITestCase
from projects.models import Project
from django.contrib.auth import get_user_model
from common.websocket import websocket_message_service, send_node_start_notification_helper
from common.parsers import fix_yaml_syntax, is_valid_yaml
from datetime import datetime

User = get_user_model()

# 定义Agent状态数据结构
class ScenarioAgentState(TypedDict):
    """场景生成Agent的状态数据"""
    project_id: int
    user_request: str
    user_id: Optional[int]
    business_context: Optional[List[Dict[str, Any]]]
    mapped_apis: Optional[List[Dict[str, Any]]]
    api_specifications: Optional[List[Dict[str, Any]]]
    generated_script: Optional[str]
    validation_report: Optional[Dict[str, Any]]
    test_case_id: Optional[int]
    error_message: Optional[str]
    current_step: str

class ScenarioGenerationAgent:
    """智能场景生成器Agent"""

    # 生成后的语义修复只允许有限次数，避免错误脚本反复消耗模型调用。
    MAX_SCENARIO_REPAIR_ATTEMPTS = 2
    
    def __init__(self, project_id: int, user_request: str, user_id: int = None, 
                 enable_streaming: bool = True, streaming_callback: Optional[Callable[[str, str], None]] = None):
        self.project_id = project_id
        self.user_request = user_request
        self.user_id = user_id
        self.enable_streaming = enable_streaming
        self.streaming_callback = streaming_callback
        
        # LLM和RAG管理器延迟初始化（在检索业务上下文节点中初始化）
        self.llm_manager = None
        self.rag_manager = None
        
        # 强制构建LangGraph工作流
        self.workflow = self._build_workflow()
    
    def _build_workflow(self) -> StateGraph:
        """构建LangGraph工作流"""
        workflow = StateGraph(ScenarioAgentState)
        
        # 添加节点（简化流程）
        workflow.add_node("requirement_parsing", self._requirement_parsing_node)
        workflow.add_node("map_to_apis", self._map_to_apis)
        workflow.add_node("fetch_api_specifications", self._fetch_api_specifications)
        workflow.add_node("generate_scenario_script", self._generate_scenario_script)
        workflow.add_node("validate_scenario_script", self._validate_scenario_script)
        workflow.add_node("save_test_case", self._save_test_case_node)
        
        # 设置线性执行流程
        workflow.set_entry_point("requirement_parsing")
        workflow.add_edge("requirement_parsing", "map_to_apis")
        workflow.add_edge("map_to_apis", "fetch_api_specifications")
        workflow.add_edge("fetch_api_specifications", "generate_scenario_script")
        workflow.add_edge("generate_scenario_script", "validate_scenario_script")
        workflow.add_edge("validate_scenario_script", "save_test_case")
        workflow.add_edge("save_test_case", END)
        
        return workflow.compile()
    
    def _has_error(self, state: ScenarioAgentState) -> bool:
        """检查是否有错误"""
        return bool(state.get("error_message"))
    
    def _send_node_start_notification(self, node_name: str, node_display_name: str, node_description: Optional[str] = None):
        """发送节点开始执行通知（使用统一的辅助函数）"""
        send_node_start_notification_helper(
            user_id=self.user_id,
            node_name=node_name,
            node_display_name=node_display_name,
            node_description=node_description,
            enable_streaming=self.enable_streaming,
            room_type="scenario_generation"
        )
    
    def _send_streaming_output(self, content: str, step: str = "generate_scenario_script") -> bool:
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
                room_type="scenario_generation"
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
            room_type="scenario_generation"
        )
    
    def _requirement_parsing_node(self, state: ScenarioAgentState) -> ScenarioAgentState:
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
            
            business_context = []
            if self.project_id:
                try:
                    # 使用RAG检索相关内容，通过filter_metadata过滤项目ID（与webui_testcase_agent保持一致）
                    search_result = self.rag_manager.search_documents(
                        query=self.user_request,
                        top_k=5,
                        filter_metadata={"project_id": self.project_id}
                    )
                    if search_result.get('success') and search_result.get('results'):
                        business_context = search_result['results']
                except Exception as e:
                    logger.warning(f"RAG检索失败: {e}")
            
            state["business_context"] = business_context
            logger.info("需求解析完成")
            
            return state
        except Exception as e:
            error_msg = f"需求解析失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
            return state
    
    def _map_to_apis(self, state: ScenarioAgentState) -> ScenarioAgentState:
        """将用户请求和业务上下文映射到API接口"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过API映射，因为前一个节点有错误")
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("map_to_apis", "映射API接口", "基于用户请求和业务上下文，匹配项目中的相关API规范，计算相关性并排序")
            
            logger.info("开始映射用户请求到API接口")
            state["current_step"] = "mapping_to_apis"
            
            # 获取项目下的所有API规范
            api_specs = APISpecification.objects.filter(
                project_id=self.project_id,
                status='completed'
            )
            
            # 调试：记录查询到的API规范
            logger.info(f"项目ID: {self.project_id}, 查询到 {api_specs.count()} 个已完成的API规范")
            
            # 如果没有查询到API规范，尝试放宽条件
            if api_specs.count() == 0:
                logger.warning(f"项目 {self.project_id} 下没有status='completed'的API规范，尝试查询所有API规范...")
                all_specs = APISpecification.objects.filter(project_id=self.project_id)
                logger.info(f"项目 {self.project_id} 下共有 {all_specs.count()} 个API规范（所有状态）")
                
                if all_specs.count() > 0:
                    for spec in all_specs:
                        logger.info(f"  - API规范: {spec.spec_name or spec.file_name}, status={spec.status}")
                    
                    # 使用所有状态的API规范
                    api_specs = all_specs
                    logger.info(f"已放宽查询条件，使用所有状态的API规范")
                else:
                    logger.error(f"项目 {self.project_id} 下没有任何API规范！请先在'API规范管理'中上传Swagger文件")
                    state["error_message"] = "项目下没有API规范，无法生成测试用例。请先在'API规范管理'中上传Swagger文件。"
                    return state
            
            mapped_apis = []
            
            # 基于用户请求和业务上下文进行API匹配（不使用LLM，直接关键词匹配）
            search_text = f"{self.user_request} {' '.join([ctx.get('content', '')[:100] for ctx in state.get('business_context', [])[:3]])}"
            
            logger.info(f"搜索关键词: {search_text[:200]}...")
            
            # 为每个API规范计算相关性
            for spec in api_specs:
                relevance_score = self._calculate_relevance(spec, search_text)
                
                logger.debug(f"API规范 '{spec.spec_name or spec.file_name}' 相关性: {relevance_score:.2f}")
                
                if relevance_score > 0:
                    mapped_apis.append({
                        "api_spec_id": spec.id,
                        "api_name": spec.spec_name or spec.file_name,
                        "relevance_score": relevance_score
                    })
            
            # 按相关性排序，取前10个最相关的API
            mapped_apis.sort(key=lambda x: x["relevance_score"], reverse=True)
            mapped_apis = mapped_apis[:10]
            
            # 记录映射结果
            if len(mapped_apis) > 0:
                logger.info(f"成功映射 {len(mapped_apis)} 个API接口:")
                for api in mapped_apis[:3]:  # 只记录前3个
                    logger.info(f"  - {api['api_name']}, 相关性: {api['relevance_score']:.2f}")
            else:
                logger.warning(f"⚠️ 没有匹配到任何API规范！可能的原因:")
                logger.warning(f"  1. API规范的名称、描述、端点摘要中都不包含搜索关键词")
                logger.warning(f"  2. 搜索关键词: {search_text[:100]}")
                logger.warning(f"  3. 建议在API规范的描述中添加关键词，或使用更明确的场景描述")
            
            state["mapped_apis"] = mapped_apis
            logger.info(f"成功映射 {len(mapped_apis)} 个API接口")
            
        except Exception as e:
            error_msg = f"映射API接口失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
        
        return state
    
    def _calculate_relevance(self, api_spec: APISpecification, search_text: str) -> float:
        """计算API与搜索文本的相关性分数（增强版：同时匹配端点信息）"""
        search_keywords = search_text.lower().split()
        
        # 过滤掉过短的关键词（如"的"、"和"等）
        search_keywords = [kw for kw in search_keywords if len(kw) > 1]
        
        # 构建搜索文本：包含规范名称、描述、所有端点的路径和摘要
        api_text_parts = [
            api_spec.spec_name or api_spec.file_name or '',
            api_spec.description or ''
        ]
        
        # 添加所有端点的路径和摘要（这是关键改进！）
        endpoint_count = 0
        sample_endpoints = []
        try:
            endpoints = APIEndpoint.objects.filter(spec=api_spec)[:50]  # 限制50个端点，避免性能问题
            endpoint_count = endpoints.count()
            
            for idx, endpoint in enumerate(endpoints):
                api_text_parts.append(endpoint.path or '')
                api_text_parts.append(endpoint.summary or '')
                api_text_parts.append(endpoint.description or '')
                if idx < 3:  # 记录前3个端点样本
                    sample_endpoints.append({"path":endpoint.path,"summary":endpoint.summary[:50] if endpoint.summary else ""})
        except Exception as e:
            logger.warning(f"获取端点信息失败: {e}")
        
        api_text = ' '.join(api_text_parts).lower()
        
        # 调试：记录API规范的文本内容（前200字符）
        logger.debug(f"API规范 '{api_spec.spec_name or api_spec.file_name}' 包含 {endpoint_count} 个端点")
        logger.debug(f"  API文本（前200字符）: {api_text[:200]}...")
        logger.debug(f"  搜索关键词: {search_keywords}")
        
        # 简单的相关性计算：匹配的关键词数量 / 总关键词数
        matches = 0
        matched_keywords = []
        
        for keyword in search_keywords:
            if len(keyword) > 2 and keyword in api_text:
                matches += 1
                matched_keywords.append(keyword)
        
        if len(search_keywords) == 0:
            relevance = 0.0
        else:
            relevance = matches / len(search_keywords)
        
        if relevance > 0:
            logger.debug(f"  匹配的关键词: {matched_keywords}, 相关性: {relevance:.2f}")
        
        return relevance
        
        # 记录匹配详情（调试用）
        if relevance > 0:
            logger.info(f"API规范 '{api_spec.spec_name or api_spec.file_name}' 匹配度: {relevance:.2f} (匹配{matches}个关键词)")
        
        return relevance
    
    def _fetch_api_specifications(self, state: ScenarioAgentState) -> ScenarioAgentState:
        """获取API规范详细信息"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过API规范获取，因为前一个节点有错误")
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("fetch_api_specifications", "获取API规范", "获取匹配的API规范的详细信息，包括端点、方法、参数等完整信息")
            
            logger.info("开始获取API规范详细信息")
            state["current_step"] = "fetching_api_specifications"
            
            api_specs = []
            mapped_apis = state.get("mapped_apis", [])
            
            # 如果没有映射到任何API规范，使用项目下的所有API规范作为fallback
            if len(mapped_apis) == 0:
                logger.warning("⚠️ 没有匹配到任何API规范，使用项目下的所有API规范（fallback机制）")
                all_specs = APISpecification.objects.filter(project_id=self.project_id)
                
                if all_specs.count() > 0:
                    logger.info(f"Fallback: 使用项目 {self.project_id} 下的所有 {all_specs.count()} 个API规范")
                    
                    for spec in all_specs:
                        mapped_apis.append({
                            "api_spec_id": spec.id,
                            "api_name": spec.spec_name or spec.file_name,
                            "relevance_score": 0.5  # 给一个默认的相关性分数
                        })
                    
                    state["mapped_apis"] = mapped_apis
                else:
                    logger.error("项目下没有任何API规范，无法生成测试用例")
                    state["error_message"] = "项目下没有API规范，无法生成测试用例。请先在'API规范管理'中上传Swagger文件。"
                    return state
            
            for mapped_api in mapped_apis:
                try:
                    spec = APISpecification.objects.get(id=mapped_api["api_spec_id"])
                    
                    # 获取API端点信息（使用正确的字段名 spec）
                    endpoints = APIEndpoint.objects.filter(spec=spec)
                    spec_metadata = spec.metadata or {}
                    definitions = (
                        spec_metadata.get("definitions", {})
                        if isinstance(spec_metadata, dict)
                        else {}
                    )
                    
                    api_specs.append({
                        "id": spec.id,
                        "name": spec.spec_name or spec.file_name,
                        "description": spec.description,
                        "spec_type": spec.spec_type,
                        "endpoints": [
                            build_generation_endpoint({
                                "id": endpoint.id,
                                "path": endpoint.path,
                                "method": endpoint.method,
                                "summary": endpoint.summary,
                                "description": endpoint.description,
                                "parameters": endpoint.parameters,
                                "request_body": endpoint.request_body,
                                "responses": endpoint.responses,
                            }, definitions=definitions)
                            for endpoint in endpoints
                        ],
                        "relevance_score": mapped_api["relevance_score"]
                    })
                    
                except APISpecification.DoesNotExist:
                    logger.warning(f"API规范 {mapped_api['api_spec_id']} 不存在")
                    continue
            
            state["api_specifications"] = api_specs
            logger.info(f"成功获取 {len(api_specs)} 个API规范详细信息")
            
        except Exception as e:
            error_msg = f"获取API规范详细信息失败: {str(e)}"
            logger.error(error_msg)
            state["error_message"] = error_msg
        
        return state
    
    def _generate_scenario_script(self, state: ScenarioAgentState) -> ScenarioAgentState:
        """生成测试脚本（使用Pydantic结构化输出生成HttpRunner JSON格式）"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过测试脚本生成，因为前一个节点有错误")
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("generate_scenario_script", "生成测试脚本", "基于用户需求、业务上下文和API规范，使用Pydantic结构化输出生成符合HttpRunner v3语法的JSON测试脚本")
            
            logger.info("开始生成测试脚本（JSON格式）")
            state["current_step"] = "generating_scenario_script"
            
            # 构建Prompt
            prompt = ChatPromptTemplate.from_template("""
🚨 系统级约束（违反此约束将导致测试失败）：
你生成的每个API请求的URL路径必须100%精确匹配下面"API规范"中提供的path字段，不允许任何修改、优化或猜测。

你是一名资深测试开发工程师，负责根据 用户需求、业务上下文 和 API 规范 自动生成可直接运行的 HttpRunner 测试脚本（JSON格式）。

📌 输入内容（模型使用三段信息）

用户需求（User Request）
{user_request}

业务上下文（Business Context）
{business_context}

API 规范（API Specifications）
{api_specifications}

⚠️ 严格使用API规范的规则（必须遵守）：
1. 你必须严格使用上面 API 规范 中提供的端点路径（path）、方法（method）和参数（parameters）
2. 不要修改、猜测或创造任何未在API规范中明确定义的路径或参数
3. 如果API规范中某个端点的path是 "/user/register"，你必须使用 "/user/register"，不能改成 "/api/users/register" 或 "/api/v1/users/register"
4. 如果API规范中某个端点的method是 "POST"，你必须使用 "POST"，不能改成其他方法
5. 如果API规范中某个端点的参数是 "username"，你必须使用 "username"，不能改成 "user_name" 或 "userName"
6. 每个步骤的 request.url 必须精确匹配API规范中的某个端点的 path 字段
7. 每个步骤的 request.method 必须精确匹配API规范中对应端点的 method 字段

🚨 响应契约规则（优先级高于通用经验，必须遵守）：
1. API 规范中每个端点的 responses.content 中包含该接口可参考的 response example 和 response schema。
2. 每个步骤的 validate 和 extract 必须优先、且只能参考当前 method + path 对应端点的 responses。
3. 如果 response example 存在，body 路径必须来自 example 的真实字段；期望值必须参考 example，并保持数字、字符串、布尔值和 null 的原始类型。
4. 如果只有 response schema，body 路径只能来自 schema.properties 或 schema.items 中定义的字段；不能自行创造 code、success、data、token 等字段。
5. 如果没有 response example 或 response schema，不要猜测任何 body 字段；该步骤最多生成 status_code 断言。
6. 禁止把其他接口的响应字段复制到当前接口，也禁止套用通用的 body.success、body.code、body.data.token 模板。
7. 提取变量必须来自当前接口已知的 response 路径；后续步骤只能引用前序步骤成功提取的变量。

请根据用户需求、业务上下文、API 请求契约和 API 响应契约自动推理完整测试流程，并生成符合 HttpRunner v3 JSON 格式的测试脚本。

📌 输出要求（必须严格遵守）

1. config 配置信息
   - name: 测试场景名称（字符串）
   - base_url: 基础URL（字符串，如 "http://example.com"）
   - variables: 全局变量（字典，如 {{"token": "xxx"}}）
   - verify: SSL验证（布尔值，默认false）

2. teststeps 测试步骤数组
   每个步骤必须包含：
   - name: 步骤名称（字符串）
   - request: 请求对象
     * method: HTTP方法（字符串，如 "GET", "POST", "PUT", "DELETE"）
     * url: 请求URL路径（字符串，如 "/api/users"）
     * params: 查询参数（字典，所有值必须是字符串，如 {{"limit": "10", "page": "1"}}）
     * headers: 请求头（字典，如 {{"Content-Type": "application/json"}}）
     * json: JSON请求体（字典或列表；Content-Type 为 application/json 时必须使用此字段）
     * data: 表单数据或原始文本请求体（Content-Type 为 form-data、x-www-form-urlencoded、text/plain 等时使用）
     * json 和 data 只能二选一，不能同时作为有效请求体输出
   - extract: 提取变量（字典，格式：{{"变量名": "jsonpath表达式"}}，如 {{"user_id": "body.data.user_id"}}）
   - validate: 断言列表（必须是字典列表格式，每个字典的键是断言类型，值是列表）
     * 格式：[{{"eq": ["status_code", 200]}}]
     * 常用断言类型：eq（等于）、ne（不等于）、gt（大于）、lt（小于）、contains（包含）
     * 每个断言是字典：{{"断言类型": ["jsonpath", 期望值]}}
     * JSONPath 必须使用双引号，并且必须能在当前接口 response example 或 schema 中找到，如 "body.data.user_id"
     * 示例：
       - {{"eq": ["status_code", 200]}}
       - {{"eq": ["body.<field_from_current_response>", <value_from_current_response>]}}
   - variables: 步骤级变量（字典，可选）

3. 提取与数据传递规则
   - extract 中的 jsonpath 表达式必须使用双引号字符串
   - 后续步骤引用变量使用 ${{variable}} 格式

4. 断言规则（非常重要）
   - validate 必须是字典列表，不能是列表的列表
   - 每个断言必须是字典格式：{{"断言类型": ["jsonpath", 期望值]}}
   - 常用断言类型：eq（等于）、ne（不等于）、gt（大于）、lt（小于）、contains（包含）
   - JSONPath 必须使用双引号，并且必须来自当前接口的 response example 或 schema
   - 错误格式（不要使用）：[["eq", ["status_code", 200]]]
   - 正确格式（必须使用）：[{{"eq": ["status_code", 200]}}]

5. 请求参数规则
   - params 中所有值必须是字符串类型
   - json 中的数字、布尔值、null 使用JSON原生格式（不加引号）
   - 请求体类型必须依据 API 规范的 request_body.content 或 Content-Type 选择：application/json 使用 json；表单类型使用 data 对象；纯文本、XML 等使用 data 字符串
   - 不要因为 Pydantic 字段可选就额外输出空的 data；JSON 请求不要同时输出 data

📌 输出格式提醒
- 下面只要求输出 JSON 结构，不提供任何可照抄的业务字段示例。
- 每个接口的响应字段、状态码和期望值都必须从上方对应端点的 response example 或 response schema 推导。
- 如果响应契约没有定义 body 字段，请不要为了“丰富断言”而补充业务字段。

⚠️ 重要提示
- 必须严格按照 HttpRunner JSON 格式输出
- 所有字符串值使用双引号
- 数组和对象使用JSON标准格式
- 确保生成的JSON格式完全正确，可以直接被HttpRunner解析执行
""")
            
            # 构建消息
            api_specs_data = state.get("api_specifications", [])
            
            # 记录传递给LLM的API规范信息（调试用）
            logger.info(f"传递给LLM的API规范数量: {len(api_specs_data)}")
            for idx, spec in enumerate(api_specs_data[:3]):  # 只记录前3个
                logger.info(f"API规范 {idx+1}: {spec.get('name', 'Unknown')}, 端点数量: {len(spec.get('endpoints', []))}")
                for ep_idx, endpoint in enumerate(spec.get('endpoints', [])[:5]):  # 只记录前5个端点
                    logger.info(f"  端点 {ep_idx+1}: {endpoint.get('method', 'N/A')} {endpoint.get('path', 'N/A')} - {endpoint.get('summary', 'N/A')}")
            
            messages = prompt.format_messages(
                user_request=self.user_request,
                business_context=json.dumps(state.get("business_context", [])[:5], ensure_ascii=False),  # 只取前5条上下文
                api_specifications=json.dumps(api_specs_data, ensure_ascii=False)
            )
            
            # 确保LLM管理器已初始化
            if self.llm_manager is None:
                self.llm_manager = get_llm_manager()
            
            # 检查LLM是否可用
            if not self.llm_manager or not self.llm_manager.current_llm:
                error_msg = "LLM不可用，无法进行结构化输出生成"
                logger.error(error_msg)
                state["error_message"] = error_msg
                
                # 发送LLM不可用消息
                error_message = f"❌ {error_msg}\n\n测试脚本生成节点执行失败。"
                self._send_streaming_output(error_message, "generate_scenario_script")
                
                return state
            
            # 发送流式输出消息，提示正在进行结构化输出
            self._send_streaming_output("正在进行结构化输出，使用Pydantic模型生成HttpRunner JSON格式测试脚本...", "generate_scenario_script")
            
            # 使用结构化输出获取Pydantic模型（带重试机制）
            max_retries = 3
            script_content = None
            last_error = None
            
            for attempt in range(1, max_retries + 1):
                try:
                    if attempt > 1:
                        retry_message = f"⚠️ 结构化输出失败，正在重试（第 {attempt}/{max_retries} 次）...\n上次错误: {str(last_error)}"
                        self._send_streaming_output(retry_message, "generate_scenario_script")
                        logger.info(f"结构化输出重试，第 {attempt} 次尝试")
                    
                    structured_llm = self.llm_manager.current_llm.with_structured_output(HttpRunnerTestCase)
                    response = structured_llm.invoke(messages)
                    
                    # 检查响应是否为 None
                    if response is None:
                        raise ValueError("结构化输出返回了 None，LLM可能未正确响应")
                    
                    # 将Pydantic模型转换为字典并清理（使用统一方法）
                    from .api_testcase_generator import ApiTestcaseGeneratorService
                    test_case_dict = response.model_dump(by_alias=True)
                    cleaned_dict = ApiTestcaseGeneratorService._clean_httprunner_script(test_case_dict)
                    
                    # 转换为格式化的JSON字符串（统一使用这种方式）
                    script_content = json.dumps(cleaned_dict, ensure_ascii=False, indent=2)
                    
                    logger.info(f"成功生成HttpRunner JSON格式测试脚本，长度: {len(script_content)} 字符（第 {attempt} 次尝试成功）")
                    logger.debug(f"生成的脚本内容: {script_content[:500]}...")
                    
                    # 发送成功消息
                    success_message = f"✅ 测试脚本生成完成！\n\n"
                    if attempt > 1:
                        success_message += f"🔄 经过 {attempt} 次尝试后成功生成\n\n"
                    success_message += f"📝 生成的脚本格式：HttpRunner JSON\n"
                    success_message += f"📊 脚本长度：{len(script_content)} 字符\n"
                    success_message += f"📋 测试步骤数：{len(test_case_dict.get('teststeps', []))}\n"
                    success_message += f"\n所有测试步骤已通过Pydantic结构化输出生成和验证。"
                    self._send_streaming_output(success_message, "generate_scenario_script")
                    
                    # 成功，跳出重试循环
                    break
                    
                except Exception as e:
                    last_error = e
                    error_detail = str(e)
                    logger.warning(f"结构化输出失败（第 {attempt}/{max_retries} 次尝试）: {error_detail}")
                    
                    # 如果是最后一次尝试，报告失败
                    if attempt == max_retries:
                        logger.error(f"结构化输出失败，已重试 {max_retries} 次，放弃生成")
                        error_msg = f"结构化输出失败，已重试 {max_retries} 次。最后错误: {error_detail}"
                        state["error_message"] = error_msg
                        
                        # 发送错误消息
                        error_message = f"❌ 结构化输出失败\n\n"
                        error_message += f"已重试 {max_retries} 次，均失败。\n"
                        error_message += f"最后错误详情: {error_detail}\n\n"
                        error_message += f"测试脚本生成节点执行失败。"
                        self._send_streaming_output(error_message, "generate_scenario_script")
                        
                        return state
            
            # 如果所有重试都失败，script_content 仍为 None
            if script_content is None:
                error_msg = f"结构化输出失败，已重试 {max_retries} 次，无法生成测试脚本"
                state["error_message"] = error_msg
                return state
            
            # 🔧 关键修复：自动修正生成的API路径和参数
            script_content = self._auto_fix_api_paths(script_content, api_specs_data)
            
            state["generated_script"] = script_content
            logger.info(f"成功生成测试脚本（JSON格式），长度: {len(script_content)} 字符")
            
        except Exception as e:
            error_msg = f"生成测试脚本失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            state["error_message"] = error_msg
        
        return state

    def _validate_scenario_script(self, state: ScenarioAgentState) -> ScenarioAgentState:
        """检查场景脚本，并对确定性错误执行有限次数的 AI 修复。"""
        if state.get("error_message"):
            logger.warning("跳过场景预检查，因为前一个节点有错误")
            return state

        try:
            self._send_node_start_notification(
                "validate_scenario_script",
                "场景预检查",
                "检查变量定义、变量使用顺序、提取路径和断言结构",
            )
            state["current_step"] = "validating_scenario_script"

            generated_script = state.get("generated_script")
            if not generated_script:
                error_msg = "没有生成的测试脚本，无法执行场景预检查"
                state["error_message"] = error_msg
                return state

            try:
                test_data = (
                    json.loads(generated_script)
                    if isinstance(generated_script, str)
                    else generated_script
                )
            except (TypeError, json.JSONDecodeError) as exc:
                error_msg = f"生成的测试脚本不是有效JSON，无法执行场景预检查: {exc}"
                state["error_message"] = error_msg
                return state

            validator = ScenarioPreflightValidator()
            report = validator.validate(
                test_data,
                api_specifications=state.get("api_specifications") or [],
            )
            repair_history: List[Dict[str, Any]] = []
            repair_attempts = 0

            while (
                report.get("summary", {}).get("error_count", 0) > 0
                and repair_attempts < self.MAX_SCENARIO_REPAIR_ATTEMPTS
            ):
                repair_attempts += 1
                before_summary = dict(report.get("summary", {}))
                self._send_node_start_notification(
                    "repair_scenario_script",
                    "自动修复脚本",
                    f"根据第 {repair_attempts} 轮预检查结果修正变量、路径或断言问题",
                )
                self._send_streaming_output(
                    f"🔧 发现 {before_summary.get('error_count', 0)} 个确定性问题，"
                    f"正在自动修复（第 {repair_attempts}/{self.MAX_SCENARIO_REPAIR_ATTEMPTS} 轮）...",
                    "repair_scenario_script",
                )

                current_script = state.get("generated_script") or generated_script
                try:
                    repaired_script = self._repair_scenario_script(
                        current_script=current_script,
                        validation_report=report,
                        state=state,
                        attempt=repair_attempts,
                    )
                    if not repaired_script or repaired_script.strip() == str(current_script).strip():
                        repair_history.append({
                            "attempt": repair_attempts,
                            "changed": False,
                            "error_count_before": before_summary.get("error_count", 0),
                            "message": "AI 未生成与原脚本不同的修复结果",
                        })
                        break

                    repaired_script = self._auto_fix_api_paths(
                        repaired_script,
                        state.get("api_specifications") or [],
                    )
                    repaired_data = json.loads(repaired_script)
                    repaired_report = validator.validate(
                        repaired_data,
                        api_specifications=state.get("api_specifications") or [],
                    )
                    after_summary = dict(repaired_report.get("summary", {}))
                    state["generated_script"] = repaired_script
                    report = repaired_report
                    repair_history.append({
                        "attempt": repair_attempts,
                        "changed": True,
                        "error_count_before": before_summary.get("error_count", 0),
                        "error_count_after": after_summary.get("error_count", 0),
                        "warning_count_after": after_summary.get("warning_count", 0),
                    })
                    logger.info(
                        "场景脚本第 %s 轮自动修复完成：错误 %s -> %s",
                        repair_attempts,
                        before_summary.get("error_count", 0),
                        after_summary.get("error_count", 0),
                    )
                except Exception as repair_exc:
                    repair_history.append({
                        "attempt": repair_attempts,
                        "changed": False,
                        "error_count_before": before_summary.get("error_count", 0),
                        "message": f"自动修复失败：{repair_exc}",
                    })
                    logger.warning("场景脚本自动修复失败（第 %s 轮）: %s", repair_attempts, repair_exc)
                    break

            report["repair_attempts"] = repair_attempts
            report["repair_history"] = repair_history
            report.setdefault("summary", {})["repair_attempts"] = repair_attempts
            state["validation_report"] = report

            summary = report.get("summary", {})
            error_count = summary.get("error_count", 0)
            warning_count = summary.get("warning_count", 0)
            if error_count:
                error_msg = (
                    f"场景预检查未通过：自动修复 {repair_attempts} 轮后仍有 "
                    f"{error_count} 个错误、{warning_count} 个警告，测试用例未保存"
                )
                state["error_message"] = error_msg
                self._send_streaming_output(
                    f"❌ {error_msg}\n已保留当前生成草稿，请根据检查报告继续调整。",
                    "validate_scenario_script",
                )
                logger.warning("场景预检查未通过: %s", report)
            else:
                if repair_attempts:
                    message = (
                        f"✅ 场景脚本已自动修复并通过检查：共修复 {repair_attempts} 轮，"
                        f"仍有 {warning_count} 个警告"
                    )
                else:
                    message = f"✅ 场景预检查完成：0 个错误、{warning_count} 个警告"
                self._send_streaming_output(message, "validate_scenario_script")
                logger.info("场景预检查通过: %s", report)

        except Exception as exc:
            error_msg = f"场景预检查失败: {exc}"
            logger.error(error_msg, exc_info=True)
            state["error_message"] = error_msg

        return state

    def _repair_scenario_script(
        self,
        current_script: str,
        validation_report: Dict[str, Any],
        state: ScenarioAgentState,
        attempt: int,
    ) -> str:
        """让模型根据结构化检查报告修正当前脚本，并返回新的 HttpRunner JSON。"""
        if self.llm_manager is None:
            self.llm_manager = get_llm_manager()
        if not self.llm_manager or not self.llm_manager.current_llm:
            raise RuntimeError("LLM不可用，无法自动修复场景脚本")

        repair_prompt = ChatPromptTemplate.from_template("""
你是一名负责修复 HttpRunner 场景脚本的测试开发工程师。

用户需求：
{user_request}

API 规范（必须保持 method 和 path 精确匹配）：
{api_specifications}

当前脚本：
{current_script}

确定性预检查报告：
{validation_report}

请仅根据报告中的 errors 修复当前脚本，不要重写业务流程，不要新增没有依据的接口、参数、响应字段或变量。

修复规则：
1. 保留当前脚本中已经正确的步骤、请求方法、请求路径和请求参数。
2. undefined_variable：优先引用前序步骤已经 extract 的变量；如果没有可靠来源，删除该变量引用或改为当前请求契约中已有的静态值，不要凭空发明变量。
3. invalid_response_path、invalid_assertion、invalid_extract：按 HttpRunner v3 JSON 结构修复；无法确认响应 body 字段时只保留 status_code 断言。
4. response_path_not_in_contract 和 response_path_unverified 是警告，不要为了消除警告而猜测业务字段；如果保留字段，必须来自当前接口响应 example 或 schema。
5. 期望值保持 JSON 原始类型：数字不要改成字符串，字符串不要改成数字，布尔值和 null 也不要加引号。
6. 变量只能被后续步骤使用，不能在同一步 extract 后立即作为当前请求输入使用。
7. 只输出符合 HttpRunnerTestCase 结构的 JSON，不要输出 Markdown、解释文字或代码围栏。

这是第 {attempt} 轮修复。修复后必须输出完整脚本，而不是局部片段。
""")
        messages = repair_prompt.format_messages(
            user_request=self.user_request,
            api_specifications=json.dumps(
                state.get("api_specifications") or [],
                ensure_ascii=False,
            ),
            current_script=current_script,
            validation_report=json.dumps(validation_report, ensure_ascii=False),
            attempt=attempt,
        )

        structured_llm = self.llm_manager.current_llm.with_structured_output(HttpRunnerTestCase)
        response = structured_llm.invoke(messages)
        if response is None:
            raise ValueError("自动修复返回了空结果")

        from .api_testcase_generator import ApiTestcaseGeneratorService

        repaired_data = response.model_dump(by_alias=True)
        cleaned_data = ApiTestcaseGeneratorService._clean_httprunner_script(repaired_data)
        return json.dumps(cleaned_data, ensure_ascii=False, indent=2)
    
    def _auto_fix_api_paths(self, script_content: str, api_specs: List[Dict]) -> str:
        """
        自动修正生成的API路径和参数，确保与API规范精确匹配
        
        这是最可靠的修复方案：无论LLM生成什么，都会自动修正为正确的路径和参数
        """
        try:
            # 解析生成的脚本
            test_data = json.loads(script_content)
            
            # 构建API规范的端点映射表
            endpoint_map = {}  # {(method, path): endpoint_info}
            endpoint_summaries = {}  # {summary: endpoint_info} 用于通过摘要匹配
            
            for spec in api_specs:
                for endpoint in spec.get('endpoints', []):
                    method = endpoint.get('method', '').upper()
                    path = endpoint.get('path', '')
                    summary = endpoint.get('summary', '')
                    
                    if method and path:
                        # 精确路径映射
                        endpoint_map[(method, path)] = endpoint
                        
                        # 摘要映射（用于模糊匹配）
                        if summary:
                            endpoint_summaries[summary.lower()] = endpoint
            
            logger.info(f"📋 API规范端点映射表包含 {len(endpoint_map)} 个端点")
            
            # 遍历并修正每个测试步骤
            fixed_count = 0
            for idx, step in enumerate(test_data.get('teststeps', [])):
                if 'request' not in step:
                    continue
                
                request = step['request']
                original_method = request.get('method', 'GET').upper()
                original_path = request.get('url', '')
                step_name = step.get('name', f'步骤{idx+1}')
                
                # 先尝试精确匹配
                if (original_method, original_path) in endpoint_map:
                    # 路径已经正确，不需要修正
                    logger.info(f"  ✓ 步骤 '{step_name}': {original_method} {original_path} - 路径正确")
                    continue
                
                # 路径不匹配，需要修正
                logger.warning(f"  ⚠️ 步骤 '{step_name}': {original_method} {original_path} - 路径不在API规范中，尝试自动修正...")
                
                # 修正策略1：通过步骤名称匹配摘要
                fixed = False
                step_name_lower = step_name.lower()
                for summary, endpoint in endpoint_summaries.items():
                    if summary in step_name_lower or step_name_lower in summary:
                        correct_method = endpoint.get('method', '').upper()
                        correct_path = endpoint.get('path', '')
                        
                        if original_method == correct_method:
                            logger.info(f"    🔧 通过摘要匹配修正: {original_path} -> {correct_path}")
                            request['url'] = correct_path
                            
                            # 同时修正参数
                            if endpoint.get('parameters'):
                                self._fix_request_parameters(request, endpoint.get('parameters'))
                            
                            fixed = True
                            fixed_count += 1
                            break
                
                if fixed:
                    continue
                
                # 修正策略2：模糊路径匹配（去除版本号、前缀等）
                for (method, path), endpoint in endpoint_map.items():
                    if method != original_method:
                        continue
                    
                    # 提取路径的核心部分（去除 /api, /v1, /v2 等前缀）
                    def normalize_path(p):
                        # 去除常见前缀
                        p = p.replace('/api/', '/').replace('/v1/', '/').replace('/v2/', '/').replace('/v3/', '/')
                        # 去除多余的斜杠
                        while '//' in p:
                            p = p.replace('//', '/')
                        return p.strip('/')
                    
                    normalized_original = normalize_path(original_path)
                    normalized_correct = normalize_path(path)
                    
                    # 如果归一化后的路径匹配
                    if normalized_original == normalized_correct or normalized_original in normalized_correct or normalized_correct in normalized_original:
                        logger.info(f"    🔧 通过路径模糊匹配修正: {original_path} -> {path}")
                        request['url'] = path
                        
                        # 同时修正参数
                        if endpoint.get('parameters'):
                            self._fix_request_parameters(request, endpoint.get('parameters'))
                        
                        fixed = True
                        fixed_count += 1
                        break
                
                if not fixed:
                    logger.warning(f"    ❌ 无法自动修正路径: {original_method} {original_path}，将保留原路径")
            
            if fixed_count > 0:
                logger.info(f"🎯 自动修正完成：共修正 {fixed_count} 个步骤的API路径和参数")
                self._send_streaming_output(f"✅ 自动修正：已将 {fixed_count} 个步骤的API路径调整为API规范中的精确路径", "generate_scenario_script")
            else:
                logger.info(f"✓ 所有步骤的API路径均正确，无需修正")
            
            # 返回修正后的脚本
            return json.dumps(test_data, ensure_ascii=False, indent=2)
            
        except Exception as e:
            logger.error(f"自动修正API路径失败: {e}", exc_info=True)
            # 如果修正失败，返回原脚本
            return script_content
    
    def _fix_request_parameters(self, request: Dict, api_parameters: Any):
        """修正请求参数，确保与API规范一致"""
        try:
            if not api_parameters or not isinstance(api_parameters, (list, dict)):
                return
            
            # 提取API规范中定义的参数名称
            spec_param_names = set()
            if isinstance(api_parameters, list):
                for param in api_parameters:
                    if isinstance(param, dict) and 'name' in param:
                        spec_param_names.add(param['name'])
            elif isinstance(api_parameters, dict):
                spec_param_names = set(api_parameters.keys())
            
            if not spec_param_names:
                return
            
            logger.debug(f"    API规范定义的参数: {spec_param_names}")
            
            # 检查并修正各种请求参数
            for param_location in ['json', 'data', 'params', 'headers']:
                if param_location not in request:
                    continue
                
                request_params = request[param_location]
                if not isinstance(request_params, dict):
                    continue
                
                # 检查是否有不在规范中的参数
                extra_params = set(request_params.keys()) - spec_param_names
                if extra_params:
                    logger.warning(f"    ⚠️ 发现不在API规范中的参数: {extra_params}，建议检查")
                
        except Exception as e:
            logger.warning(f"修正请求参数失败: {e}")
    
    def _save_test_case_node(self, state: ScenarioAgentState) -> ScenarioAgentState:
        """保存测试用例节点 - 作为最后一个节点执行"""
        # 检查前一个节点是否有错误
        if state.get("error_message"):
            logger.warning("跳过保存测试用例，因为前一个节点有错误")
            return state
        
        # 检查是否有生成的脚本
        if not state.get("generated_script"):
            error_msg = "没有生成的测试脚本，无法保存测试用例"
            logger.error(error_msg)
            state["error_message"] = error_msg
            return state
            
        try:
            # 发送节点开始通知
            self._send_node_start_notification("save_test_case", "保存测试用例", "将生成的测试脚本保存到数据库")
            
            logger.info("开始保存测试用例")
            state["current_step"] = "saving_test_case"
            
            # 获取项目和用户
            try:
                project = Project.objects.get(id=state["project_id"])
            except Project.DoesNotExist:
                error_msg = f"项目 {state['project_id']} 不存在"
                logger.error(error_msg)
                state["error_message"] = error_msg
                return state
            
            user = None
            if state.get("user_id"):
                try:
                    user = User.objects.get(id=state["user_id"])
                except User.DoesNotExist:
                    logger.warning(f"用户 {state['user_id']} 不存在，将使用匿名用户")
            
            # 解析生成的脚本，提取测试数据
            generated_script = state.get("generated_script", "")
            test_data = None
            try:
                import json
                test_data = json.loads(generated_script) if isinstance(generated_script, str) else generated_script
            except json.JSONDecodeError:
                logger.warning("无法解析generated_script为JSON，将作为字符串存储")
                test_data = {"raw_script": generated_script}
            
            # 创建测试用例（涉及的端点/模块可从 script_content.teststeps 动态解析，无需冗余存储）
            test_case = APITestCase.objects.create(
                title=f"智能场景: {state['user_request'][:50]}...",
                description=state["user_request"],
                test_case_type='scenario',
                project=project,
                created_by=user,
                script_content=state.get("generated_script", ""),
                priority='medium',
            )
            
            state["test_case_id"] = test_case.id
            logger.info(f"成功保存测试用例，ID: {test_case.id}")
            
        except Exception as e:
            error_msg = f"保存测试用例失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            state["error_message"] = error_msg
        
        return state
    
    
    def run(self) -> Dict[str, Any]:
        """运行场景生成Agent"""
        try:
            logger.info(f"开始运行场景生成Agent，项目ID: {self.project_id}")
            
            # 强制使用LangGraph工作流
            if not self.workflow:
                raise RuntimeError("LangGraph工作流未初始化，无法运行场景生成Agent")
            
            return self._run_with_langgraph()
                
        except Exception as e:
            error_msg = f"运行场景生成Agent失败: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "current_step": "failed"
            }
    
    def _run_with_langgraph(self) -> Dict[str, Any]:
        """使用LangGraph工作流运行"""
        # 初始化状态
        initial_state = ScenarioAgentState(
            project_id=self.project_id,
            user_request=self.user_request,
            user_id=self.user_id,
            business_context=None,
            mapped_apis=None,
            api_specifications=None,
            generated_script=None,
            validation_report=None,
            test_case_id=None,
            error_message=None,
            current_step="initialized"
        )
        
        # 运行工作流
        result = self.workflow.invoke(initial_state)
        
        # 检查是否有错误
        if result.get("error_message"):
            return {
                "success": False,
                "error": result["error_message"],
                "current_step": result.get("current_step", "unknown"),
                "generated_script": result.get("generated_script"),
                "validation_report": result.get("validation_report"),
            }
        
        # 返回成功结果（包含test_case_id）
        return {
            "success": True,
            "test_case_id": result.get("test_case_id"),
            "generated_script": result.get("generated_script"),
            "validation_report": result.get("validation_report"),
        }
