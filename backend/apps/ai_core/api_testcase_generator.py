"""
API测试用例生成服务
使用多LLM兼容管理器生成测试用例和业务流程
"""
import json
import logging
from typing import Dict, List, Any, Optional, Union
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from django.db import transaction
from django.contrib.auth import get_user_model
from ai_core.model_manager import get_llm_manager

logger = logging.getLogger(__name__)

# 延迟导入Django模型，避免循环导入
User = get_user_model()

# ============ Pydantic 模型定义 - HttpRunner JSON 格式 ============
# 这些模型用于统一生成 HttpRunner 测试脚本（端点和场景都使用）

class HttpRunnerRequest(BaseModel):
    """HttpRunner 请求结构"""
    method: str = Field(description="HTTP方法，如 GET, POST, PUT, DELETE 等")
    url: str = Field(description="请求URL路径")
    params: Dict[str, Any] = Field(default_factory=dict, description="查询参数")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头")
    json: Optional[Union[Dict, List]] = Field(default=None, description="JSON请求体")
    data: Optional[Union[str, Dict[str, Any]]] = Field(default=None, description="表单数据")
    cookies: Dict[str, str] = Field(default_factory=dict, description="Cookies")
    timeout: float = Field(default=120, description="请求超时时间（秒）")


class HttpRunnerTestStep(BaseModel):
    """HttpRunner 测试步骤"""
    name: str = Field(description="步骤名称")
    request: HttpRunnerRequest = Field(description="请求信息")
    extract: Dict[str, str] = Field(default_factory=dict, description="提取变量，格式：{'变量名': 'jsonpath表达式'}")
    validate: List[Dict[str, Any]] = Field(default_factory=list, description="断言列表，必须是字典列表格式，如：[{'eq': ['status_code', 200]}, {'eq': ['body.success', True]}]。每个字典的键是断言类型（eq/ne/gt/lt/contains等），值是列表[jsonpath, 期望值]")
    variables: Dict[str, Any] = Field(default_factory=dict, description="步骤级变量")


class HttpRunnerConfig(BaseModel):
    """HttpRunner 配置"""
    name: str = Field(description="测试场景名称")
    base_url: str = Field(default="", description="基础URL")
    variables: Dict[str, Any] = Field(default_factory=dict, description="全局变量")
    verify: bool = Field(default=False, description="是否验证SSL证书")


class HttpRunnerTestCase(BaseModel):
    """HttpRunner 测试用例结构（JSON格式）"""
    config: HttpRunnerConfig = Field(description="测试配置")
    teststeps: List[HttpRunnerTestStep] = Field(description="测试步骤列表")


# Pydantic 模型定义
class TestCaseVariables(BaseModel):
    """测试用例变量"""
    path_params: Dict[str, Any] = Field(default_factory=dict, description="路径参数")
    query_params: Dict[str, Any] = Field(default_factory=dict, description="查询参数")
    body: Dict[str, Any] = Field(default_factory=dict, description="请求体")


class TestCase(BaseModel):
    """单个测试用例"""
    title: str = Field(description="测试用例标题")
    type: str = Field(description="测试类型：positive/negative/boundary")
    description: str = Field(description="测试描述")
    variables: TestCaseVariables = Field(description="测试变量")
    expected_status_code: int = Field(description="预期状态码")


class TestCasesResponse(BaseModel):
    """测试用例响应结构"""
    positive_case: List[TestCase] = Field(default_factory=list, description="正向测试用例数组")
    negative_cases: List[TestCase] = Field(default_factory=list, description="负向测试用例数组")
    boundary_cases: List[TestCase] = Field(default_factory=list, description="边界测试用例数组")


class ApiTestcaseGeneratorService:
    """API测试用例生成服务"""
    
    # 测试类型映射常量
    TYPE_MAPPING = {
        'positive': 'positive_case',
        'negative': 'negative_cases',
        'boundary': 'boundary_cases'
    }
    
    # 测试用例类型列表
    CASE_TYPES = ['positive_case', 'negative_cases', 'boundary_cases']
    
    # 空测试用例结构（用于错误返回）
    EMPTY_CASES = {
        'positive_case': [],
        'negative_cases': [],
        'boundary_cases': []
    }
    
    # 空保存结果结构
    EMPTY_SAVE_RESULT = {
        'created_cases': [],
        'save_errors': [],
        'cases_generated': 0,
        'cases_saved': 0
    }
    
    def __init__(self):
        """初始化API测试用例生成服务"""
        try:
            self.llm_manager = get_llm_manager()
            self.use_ai = self.llm_manager.current_llm is not None
            
            if self.use_ai:
                logger.info(f"AI测试生成服务初始化成功，使用LLM: {self.llm_manager.llm_type}")
            else:
                logger.warning("未配置任何LLM")
                
        except Exception as e:
            logger.warning(f"LLM管理器初始化失败: {str(e)}")
            self.llm_manager = None
            self.use_ai = False
    
    def generate_endpoint_test_cases(self, endpoint_details: Dict[str, Any], case_types: List[str], test_type_configs: Dict[str, int] = None) -> Dict[str, Any]:
        """
        为单个端点生成测试用例
        
        Args:
            endpoint_details: 端点详情
            case_types: 需要生成的测试类型列表
            test_type_configs: 测试类型配置，指定每种类型生成的数量
            
        Returns:
            生成的测试用例
        """
        if not self.use_ai or not self.llm_manager.current_llm:
            return {
                'success': False,
                'error': 'LLM服务不可用，请检查LLM配置或网络连接'
            }
        
        return self._generate_with_ai(endpoint_details, case_types, test_type_configs)
    
    def _generate_with_ai(self, endpoint_details: Dict[str, Any], case_types: List[str], test_type_configs: Dict[str, int] = None) -> Dict[str, Any]:
        """使用AI生成测试用例（使用Pydantic结构化输出）"""
        try:
            # 构建提示词和消息
            prompt = self._build_endpoint_test_prompt(endpoint_details, case_types, test_type_configs)
            messages = [
                SystemMessage(content=self._get_system_prompt()),
                HumanMessage(content=prompt)
            ]
            
            # 使用结构化输出获取Pydantic模型
            structured_llm = self.llm_manager.current_llm.with_structured_output(TestCasesResponse)
            response = structured_llm.invoke(messages)
            
            # 将Pydantic模型转换为字典
            test_cases = response.model_dump()
            
            # 验证返回的测试用例数量是否符合要求
            if test_type_configs:
                for test_type, expected_count in test_type_configs.items():
                    if expected_count > 0:
                        field_name = self.TYPE_MAPPING.get(test_type, test_type)
                        actual_count = len(test_cases.get(field_name, []))
                        if actual_count != expected_count:
                            logger.warning(
                                f"测试用例数量不匹配: {test_type}类型要求{expected_count}个，实际返回{actual_count}个"
                            )
            
            return {
                'success': True,
                'test_cases': test_cases,
                'generation_method': self.llm_manager.llm_type,
                'model_info': self.llm_manager.get_model_info()
            }
            
        except Exception as e:
            logger.error(f"AI生成失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': f'AI生成测试用例失败: {str(e)}'
            }
    
    def _build_endpoint_test_prompt(self, endpoint_details: Dict[str, Any], case_types: List[str], test_type_configs: Dict[str, int] = None) -> str:
        """构建端点测试生成提示词"""
        # 提取端点信息
        path = endpoint_details.get('path', '')
        method = endpoint_details.get('method', '')
        summary = endpoint_details.get('summary', '')
        description = endpoint_details.get('description', '')
        
        # 格式化JSON数据
        params_json = json.dumps(endpoint_details.get('parameters', []), ensure_ascii=False, indent=2)
        body_schema_json = json.dumps(endpoint_details.get('request_body', {}), ensure_ascii=False, indent=2)
        responses_json = json.dumps(endpoint_details.get('responses', {}), ensure_ascii=False, indent=2)
        
        # 构建测试类型要求（明确映射到返回字段名）
        if test_type_configs:
            test_requirements = []
            for test_type, count in test_type_configs.items():
                if count > 0:
                    field_name = self.TYPE_MAPPING.get(test_type, test_type)
                    test_requirements.append(f"- {test_type}（对应字段 {field_name}）: {count}个")
        else:
            test_requirements = [f"- {case_type}: 3个" for case_type in case_types]
        
        test_requirements_text = "\n".join(test_requirements)
        
        prompt = f"""
请为以下API端点生成测试用例，并以JSON格式返回结果：

**API端点信息：**
- 路径: {path}
- 方法: {method}
- 摘要: {summary}
- 描述: {description}

**参数信息：**
{params_json}

**请求体结构：**
{body_schema_json}

**响应定义：**
{responses_json}

**需要生成的测试类型和数量：**
{test_requirements_text}

**重要要求（必须严格遵守）：**
1. **必须严格按照上面指定的数量生成每种类型的测试用例，不能返回空数组（除非明确要求0个）**
2. **如果要求生成3个正向测试用例，positive_case数组必须包含exactly 3个测试用例，不能是0个或空数组**
3. **如果要求生成3个负向测试用例，negative_cases数组必须包含exactly 3个测试用例，不能是0个或空数组**
4. **如果要求生成3个边界测试用例，boundary_cases数组必须包含exactly 3个测试用例，不能是0个或空数组**
5. 如果某种类型要求0个，则返回空数组
6. 测试数据必须是实际可用的，符合API规范
7. 正向测试用例（positive）使用有效数据，预期状态码200
8. 负向测试用例（negative）使用无效数据，预期状态码400/404/500等
9. 边界测试用例（boundary）使用边界值数据
10. 请以JSON格式返回结果

**输出格式要求（严格对应）：**
返回的JSON必须包含以下字段，每个字段都是数组：
- positive_case: 正向测试用例数组（如果要求生成N个，必须包含exactly N个，不能为空）
- negative_cases: 负向测试用例数组（如果要求生成N个，必须包含exactly N个，不能为空）
- boundary_cases: 边界测试用例数组（如果要求生成N个，必须包含exactly N个，不能为空）

**字段映射关系：**
- positive → positive_case
- negative → negative_cases
- boundary → boundary_cases

每个测试用例必须包含：title（标题）、type（类型，必须与字段对应：positive/negative/boundary）、description（描述）、variables（变量对象，包含path_params、query_params、body）、expected_status_code（预期状态码）。"""
        
        return prompt
    
    
    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return """你是一个专业的API测试工程师，擅长分析OpenAPI规范并生成全面的测试用例。

你的任务是为API端点生成测试用例，包括：
1. 正向测试用例（positive）：验证正常功能，使用有效数据，预期状态码200
2. 负向测试用例（negative）：验证错误处理，使用无效数据，预期状态码400/404/500等
3. 边界测试用例（boundary）：验证边界条件，使用边界值数据

**关键要求（必须严格遵守）：**
1. **你必须严格按照用户指定的数量生成每种类型的测试用例**
2. **如果用户要求生成N个正向测试用例，positive_case数组必须包含exactly N个测试用例，不能是0个或空数组**
3. **如果用户要求生成N个负向测试用例，negative_cases数组必须包含exactly N个测试用例，不能是0个或空数组**
4. **如果用户要求生成N个边界测试用例，boundary_cases数组必须包含exactly N个测试用例，不能是0个或空数组**
5. **只有在用户明确要求0个时，才能返回空数组**

**字段映射关系（必须严格遵守）：**
- positive → positive_case（数组）
- negative → negative_cases（数组）
- boundary → boundary_cases（数组）

每个测试用例必须包含以下字段：
- title: 测试用例标题（字符串）
- type: 测试类型（必须与字段对应：positive/negative/boundary）
- description: 测试描述（字符串）
- variables: 测试变量对象
  - path_params: 路径参数对象（字典）
  - query_params: 查询参数对象（字典）
  - body: 请求体对象（字典）
- expected_status_code: 预期状态码（整数）

请确保测试数据符合API规范且实际可用，并以JSON格式返回结果。"""
    
    
    def format_test_cases_with_config(self, test_cases: Dict[str, Any], test_type_configs: Dict[str, int]) -> Dict[str, Any]:
        """
        根据测试类型配置格式化测试用例
        
        Args:
            test_cases: 原始测试用例数据
            test_type_configs: 测试类型配置，指定每种类型生成的数量
            
        Returns:
            格式化后的测试用例字典
        """
        # 处理异常情况：解析字符串或raw_response
        test_cases = self._normalize_test_cases_input(test_cases)
        if test_cases is None:
            return self.EMPTY_CASES.copy()
        
        # 处理正常的测试用例数据
        if isinstance(test_cases, list):
            # 根据配置分配测试用例数量
            positive_count = test_type_configs.get('positive', 0)
            negative_count = test_type_configs.get('negative', 0)
            boundary_count = test_type_configs.get('boundary', 0)
            
            result = self.EMPTY_CASES.copy()
            start_idx = 0
            
            if positive_count > 0:
                result['positive_case'] = test_cases[start_idx:start_idx + positive_count]
                start_idx += positive_count
            
            if negative_count > 0:
                result['negative_cases'] = test_cases[start_idx:start_idx + negative_count]
                start_idx += negative_count
            
            if boundary_count > 0:
                result['boundary_cases'] = test_cases[start_idx:start_idx + boundary_count]
            
            return result
        elif isinstance(test_cases, dict):
            # 检查是否包含预期的测试用例键
            if any(key in test_cases for key in self.CASE_TYPES):
                # 根据配置限制每种类型的数量
                formatted_cases = self.EMPTY_CASES.copy()
                for key in self.CASE_TYPES:
                    if key in test_cases and isinstance(test_cases[key], list):
                        max_count = self._get_max_count_for_case_type(key, test_type_configs)
                        formatted_cases[key] = test_cases[key][:max_count]
                return formatted_cases
            else:
                # 如果不是标准格式，尝试推断结构
                logger.warning(f"非标准测试用例格式，尝试推断结构。键: {list(test_cases.keys())}")
                return self._infer_test_case_types(test_cases, test_type_configs)
    
    def _infer_test_case_types(self, test_cases: Dict[str, Any], test_type_configs: Dict[str, int]) -> Dict[str, Any]:
        """从非标准格式推断测试用例类型"""
        inferred_cases = self.EMPTY_CASES.copy()
        
        # 类型推断规则
        type_keywords = {
            'positive_case': ['positive', 'success'],
            'negative_cases': ['negative', 'error', 'fail'],
            'boundary_cases': ['boundary', 'edge']
        }
        
        for key, value in test_cases.items():
            if isinstance(value, list):
                key_lower = key.lower()
                assigned = False
                
                for case_type, keywords in type_keywords.items():
                    if any(kw in key_lower for kw in keywords):
                        max_count = self._get_max_count_for_case_type(case_type, test_type_configs)
                        inferred_cases[case_type] = value[:max_count]
                        assigned = True
                        break
                
                # 默认归类到正向测试用例
                if not assigned:
                    max_count = test_type_configs.get('positive', 0)
                    inferred_cases['positive_case'].extend(value[:max_count])
        
        return inferred_cases
    
    def generate_test_cases_and_script(self, endpoint_details: Dict[str, Any], case_types: List[str], 
                                      test_type_configs: Dict[str, int] = None) -> Dict[str, Any]:
        """
        生成测试用例并生成HttpRunner脚本（统一方法）
        
        Args:
            endpoint_details: 端点详情
            case_types: 需要生成的测试类型列表
            test_type_configs: 测试类型配置，指定每种类型生成的数量
            
        Returns:
            包含测试用例和脚本的字典：
            {
                'success': bool,
                'test_cases': Dict[str, Any],  # 原始测试用例
                'formatted_cases': Dict[str, Any],  # 格式化后的测试用例
                'script': str,  # HttpRunner JSON脚本
                'generation_method': str,
                'model_info': Dict,
                'error': str  # 如果失败
            }
        """
        # 步骤1: 生成测试用例
        result = self.generate_endpoint_test_cases(endpoint_details, case_types, test_type_configs)
        
        if not result.get('success'):
            return {
                'success': False,
                'error': result.get('error', 'AI生成测试用例失败'),
                'test_cases': {},
                'formatted_cases': {},
                'script': None
            }
        
        # 步骤2: 格式化测试用例
        test_cases = result.get('test_cases', {})
        formatted_cases = self.format_test_cases_with_config(test_cases, test_type_configs or {})
        
        # 步骤3: 生成HttpRunner脚本
        try:
            full_script = self.generate_httprunner_script(formatted_cases, endpoint_details)
        except Exception as script_error:
            logger.warning(f"HttpRunner脚本生成失败，但不影响测试用例: {script_error}")
            full_script = self._build_error_script_json()
        
        return {
            'success': True,
            'test_cases': test_cases,
            'formatted_cases': formatted_cases,
            'script': full_script,
            'generation_method': result.get('generation_method'),
            'model_info': result.get('model_info')
        }
    
    def save_test_cases_to_db(self, formatted_cases: Dict[str, Any], endpoint, project, 
                              user_id: int, test_type_configs: Dict[str, int]) -> Dict[str, Any]:
        """
        根据测试类型配置保存测试用例到数据库
        
        Args:
            formatted_cases: 格式化后的测试用例数据
            endpoint: API端点对象
            project: 项目对象
            user_id: 用户ID
            test_type_configs: 测试类型配置
            
        Returns:
            包含保存结果的字典：
            {
                'created_cases': List[APITestCase],
                'save_errors': List[str],
                'cases_generated': int,
                'cases_saved': int
            }
        """
        # 延迟导入，避免循环导入
        from api_testing.models import APITestCase
        
        created_cases = []
        save_errors = []
        
        try:
            # 获取用户
            user = User.objects.get(id=user_id) if user_id else None
            
            # 验证数据格式
            if not isinstance(formatted_cases, dict):
                logger.error(f"格式化后的测试用例数据格式错误，期望字典类型，实际: {type(formatted_cases)}")
                result = self.EMPTY_SAVE_RESULT.copy()
                result['save_errors'] = ['数据格式错误']
                return result
            
            # 检查是否有有效的测试用例数据
            has_valid_cases = False
            total_generated = 0
            for key, value in formatted_cases.items():
                if isinstance(value, list) and len(value) > 0:
                    has_valid_cases = True
                    total_generated += len(value)
            
            if not has_valid_cases:
                logger.warning("没有找到有效的测试用例数据")
                result = self.EMPTY_SAVE_RESULT.copy()
                result['save_errors'] = ['没有找到有效的测试用例数据']
                return result
            
            # 处理测试用例数据并保存
            if isinstance(formatted_cases, list):
                # 根据配置分配测试用例
                total_count = sum(test_type_configs.values())
                for case_data in formatted_cases[:total_count]:
                    test_case = self._try_create_test_case(case_data, endpoint, project, user, save_errors)
                    if test_case:
                        created_cases.append(test_case)
            else:
                # 如果是字典格式，处理各种类型的测试用例
                for case_type, cases in formatted_cases.items():
                    if isinstance(cases, list):
                        # 根据配置限制数量
                        max_count = self._get_max_count_for_case_type(case_type, test_type_configs)
                        if max_count == 0:
                            continue
                        
                        for case_data in cases[:max_count]:
                            test_case = self._try_create_test_case(case_data, endpoint, project, user, save_errors)
                            if test_case:
                                created_cases.append(test_case)
        
        except Exception as e:
            error_msg = f"保存测试用例到数据库过程中发生错误: {str(e)}"
            logger.error(error_msg)
            save_errors.append(error_msg)
        
        logger.info(f"成功保存 {len(created_cases)} 个测试用例到数据库")
        return {
            'created_cases': created_cases,
            'save_errors': save_errors,
            'cases_generated': total_generated,
            'cases_saved': len(created_cases)
        }
    
    def _create_test_case_from_data(self, case_data: Dict[str, Any], endpoint, project, user) -> Optional[Any]:
        """
        从测试用例数据创建APITestCase对象（端点测试用例）
        
        Args:
            case_data: 测试用例数据
            endpoint: API端点对象
            project: 项目对象
            user: 用户对象
            
        Returns:
            创建的APITestCase对象，如果创建失败返回None
        """
        # 延迟导入，避免循环导入
        from api_testing.models import APITestCase
        
        try:
            # 映射测试类型
            test_type = self._map_test_type(case_data)
            
            # 生成单个测试用例的HttpRunner脚本
            script_content = self._generate_single_test_case_script(case_data, endpoint)
            
            # 使用独立的事务创建每个测试用例，避免一个失败影响其他
            with transaction.atomic():
                # 创建端点测试用例（script_content 是唯一数据源）
                test_case = APITestCase.objects.create(
                    test_case_type='endpoint',
                    endpoint=endpoint,
                    project=project,
                    title=case_data.get('title', 'AI生成的测试用例'),
                    description=case_data.get('description', ''),
                    test_type=test_type,
                    script_content=script_content,
                    created_by=user
                )
            
            logger.info(f"成功创建端点测试用例: {test_case.title} (ID: {test_case.id})")
            return test_case
            
        except Exception as e:
            logger.error(f"创建端点测试用例失败: {str(e)}, 数据: {case_data}")
            return None
    
    def _map_test_type(self, case_data: Dict[str, Any]) -> str:
        """映射测试类型"""
        # 优先使用case_data中的type字段
        test_type = case_data.get('type', '').lower()
        if test_type in ['positive', 'negative', 'boundary']:
            return test_type
        
        # 如果没有type字段，尝试从title中推断
        title = case_data.get('title', '').lower()
        if 'positive' in title or '正向' in title or '成功' in title:
            return 'positive'
        elif 'negative' in title or '负向' in title or '失败' in title or '无效' in title:
            return 'negative'
        elif 'boundary' in title or '边界' in title or '边界值' in title:
            return 'boundary'
        else:
            return 'positive'  # 默认为正向测试
    
    def _generate_single_test_case_script(self, case_data: Dict[str, Any], endpoint) -> str:
        """为单个测试用例生成HttpRunner脚本（JSON格式）"""
        try:
            endpoint_info = {
                'path': endpoint.path,
                'method': endpoint.method,
                'summary': endpoint.summary or '',
                'description': endpoint.description or ''
            }
            
            # 根据类型分配到对应的字段
            case_type = case_data.get('type', '').lower()
            formatted_cases = self.EMPTY_CASES.copy()
            if case_type == 'positive':
                formatted_cases['positive_case'] = [case_data]
            elif case_type == 'negative':
                formatted_cases['negative_cases'] = [case_data]
            elif case_type == 'boundary':
                formatted_cases['boundary_cases'] = [case_data]
            else:
                formatted_cases['positive_case'] = [case_data]  # 默认
            
            return self.generate_httprunner_script(formatted_cases, endpoint_info)
        except Exception as e:
            logger.error(f"生成单个测试用例脚本失败: {str(e)}")
            return self._build_error_script_json()
    
    def _normalize_test_cases_input(self, test_cases: Any) -> Optional[Dict[str, Any]]:
        """规范化测试用例输入，处理字符串和raw_response情况"""
        # 处理字符串输入
        if isinstance(test_cases, str):
            logger.warning(f"AI服务返回了字符串而不是测试用例数据: {test_cases[:200]}...")
            try:
                return json.loads(test_cases)
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析失败: {e}")
                return None
        
        # 处理raw_response字段
        if isinstance(test_cases, dict) and 'raw_response' in test_cases:
            logger.warning("检测到raw_response字段，尝试提取实际测试用例数据")
            raw_response = test_cases['raw_response']
            if isinstance(raw_response, str):
                try:
                    return json.loads(raw_response)
                except json.JSONDecodeError as e:
                    logger.error(f"raw_response JSON解析失败: {e}")
                    return None
            return raw_response
        
        return test_cases
    
    def _get_max_count_for_case_type(self, case_type: str, test_type_configs: Dict[str, int]) -> int:
        """根据测试用例类型获取最大数量"""
        if case_type == 'positive_case':
            return test_type_configs.get('positive', 0)
        elif case_type == 'negative_cases':
            return test_type_configs.get('negative', 0)
        elif case_type == 'boundary_cases':
            return test_type_configs.get('boundary', 0)
        return 0
    
    def _try_create_test_case(self, case_data: Dict[str, Any], endpoint, project, user, save_errors: List[str]) -> Optional[Any]:
        """尝试创建测试用例，处理验证和错误"""
        try:
            # 验证测试用例数据格式
            if not isinstance(case_data, dict):
                error_msg = f"跳过无效的测试用例数据格式: {type(case_data)}"
                logger.warning(error_msg)
                save_errors.append(error_msg)
                return None
            
            # 检查必需的字段
            if 'title' not in case_data:
                error_msg = "跳过缺少title字段的测试用例"
                logger.warning(error_msg)
                save_errors.append(error_msg)
                return None
            
            return self._create_test_case_from_data(case_data, endpoint, project, user)
        except Exception as e:
            error_msg = f"创建测试用例失败: {str(e)}"
            logger.error(error_msg)
            save_errors.append(error_msg)
            return None
    
    def generate_httprunner_script(self, test_cases: Dict[str, Any], endpoint_details: Dict[str, Any]) -> str:
        """
        生成HttpRunner测试脚本（JSON格式）- 使用Pydantic模型统一生成
        
        Args:
            test_cases: 测试用例
            endpoint_details: 端点详情
            
        Returns:
            HttpRunner JSON脚本
        """
        try:
            # 使用Pydantic模型构建测试用例
            test_case_model = self._build_httprunner_test_case(test_cases, endpoint_details)
            
            # 转换为字典并清理（统一处理）
            script_dict = self._clean_httprunner_script(test_case_model.model_dump())
            
            # 转换为格式化的JSON字符串（统一使用这种方式）
            return json.dumps(script_dict, ensure_ascii=False, indent=2)
            
        except Exception as e:
            logger.error(f"生成HttpRunner脚本失败: {e}")
            return self._build_error_script_json()
    
    @staticmethod
    def _clean_httprunner_script(script_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        清理和规范化HttpRunner脚本（统一方法）
        确保格式符合HttpRunner要求：
        1. validate 字段必须是字典列表格式
        2. 移除 request.data 和 request.json 中的 None 值
        
        Args:
            script_dict: HttpRunner脚本字典
            
        Returns:
            清理后的脚本字典
        """
        if 'teststeps' not in script_dict:
            return script_dict
        
        for teststep in script_dict['teststeps']:
            if 'request' not in teststep:
                continue
            
            request = teststep['request']
            
            # 移除 None 值的字段（HttpRunner Pydantic模型不接受None）
            if 'data' in request and request['data'] is None:
                del request['data']
            if 'json' in request and request['json'] is None:
                del request['json']
            
            # 确保 validate 字段格式正确（字典列表格式）
            if 'validate' in teststep and isinstance(teststep['validate'], list):
                cleaned_validators = []
                for validator in teststep['validate']:
                    if isinstance(validator, list) and len(validator) >= 2:
                        # 列表格式: ["eq", ["status_code", 200]] -> 转换为字典格式
                        comparator = validator[0]
                        compare_values = validator[1] if isinstance(validator[1], list) else [validator[1]]
                        cleaned_validators.append({comparator: compare_values})
                    elif isinstance(validator, dict):
                        # 已经是字典格式，直接使用
                        cleaned_validators.append(validator)
                teststep['validate'] = cleaned_validators
        
        return script_dict
    
    def _build_httprunner_test_case(self, test_cases: Dict[str, Any], endpoint_details: Dict[str, Any]) -> HttpRunnerTestCase:
        """
        使用Pydantic模型构建HttpRunner测试用例（统一方法）
        
        Args:
            test_cases: 测试用例数据
            endpoint_details: 端点详情
            
        Returns:
            HttpRunnerTestCase Pydantic模型实例
        """
        # 构建配置
        config = self._build_config_model(endpoint_details)
        
        # 构建测试步骤
        teststeps = []
        for case_type in self.CASE_TYPES:
            if case_type in test_cases and test_cases[case_type]:
                for case in test_cases[case_type]:
                    teststep = self._build_test_step_model(case, endpoint_details)
                    if teststep:
                        teststeps.append(teststep)
        
        # 返回Pydantic模型实例
        return HttpRunnerTestCase(
            config=config,
            teststeps=teststeps
        )
    
    def _build_config_model(self, endpoint_details: Dict[str, Any]) -> HttpRunnerConfig:
        """构建HttpRunner配置Pydantic模型"""
        path = endpoint_details.get('path', '')
        return HttpRunnerConfig(
            name=f"AI生成的测试用例 - {path}",
            base_url="http://localhost:8000",
            variables={
                "endpoint_path": path
            },
            verify=False
        )
    
    def _build_test_step_model(self, test_case: Dict[str, Any], endpoint_details: Dict[str, Any]) -> Optional[HttpRunnerTestStep]:
        """构建单个测试步骤的Pydantic模型"""
        try:
            method = endpoint_details.get('method', 'GET')
            path = endpoint_details.get('path', '')
            variables = test_case.get('variables', {})
            
            # 构建请求对象
            request = self._build_request_model(method, path, variables)
            
            # 构建断言
            expected_status = test_case.get('expected_status_code', 200)
            validate = [{"eq": ["status_code", expected_status]}]
            
            # 返回测试步骤模型
            return HttpRunnerTestStep(
                name=test_case.get('title', 'Test Step'),
                request=request,
                extract={},
                validate=validate,
                variables={}
            )
        except Exception as e:
            logger.error(f"构建测试步骤模型失败: {e}")
            return None
    
    def _build_request_model(self, method: str, path: str, variables: Dict[str, Any]) -> HttpRunnerRequest:
        """构建HttpRunner请求模型"""
        # 处理路径参数
        url = self._process_path_params(path, variables.get('path_params', {}))
        
        # 构建请求数据
        request_data = {
            "method": method,
            "url": url,
            "params": {},
            "headers": {},
            "cookies": {},
            "timeout": 120
        }
        
        # 添加查询参数（所有值必须是字符串）
        query_params = variables.get('query_params', {})
        if query_params:
            request_data["params"] = {k: str(v) for k, v in query_params.items()}
        
        # 添加请求体
        body = variables.get('body', {})
        if body and method.upper() in ['POST', 'PUT', 'PATCH']:
            request_data["json"] = body
        
        return HttpRunnerRequest(**request_data)
    
    def _build_error_script_json(self) -> str:
        """构建错误脚本JSON（使用Pydantic模型）"""
        error_config = HttpRunnerConfig(
            name="生成脚本失败",
            base_url="",
            variables={},
            verify=False
        )
        error_test_case = HttpRunnerTestCase(
            config=error_config,
            teststeps=[]
        )
        return json.dumps(error_test_case.model_dump(), ensure_ascii=False, indent=2)
    
    def _process_path_params(self, path: str, path_params: Dict[str, Any]) -> str:
        """处理路径参数，替换路径中的占位符"""
        url = path
        for param_name, param_value in path_params.items():
            url = url.replace(f'{{{param_name}}}', str(param_value))
        return url
