from rest_framework import serializers
from projects.serializers import UploadedFileSerializer
from .models import (
    APISpecification, APIEndpoint, APITestCase,
    APITestExecution, APITestSuite, APITestCaseExecutionDetail,
    APITestSuiteExecutionDetail, APITestSuiteCaseExecution
)


class APIEndpointSerializer(serializers.ModelSerializer):
    """API端点序列化器"""
    module_name = serializers.SerializerMethodField()

    class Meta:
        model = APIEndpoint
        fields = ['id', 'path', 'method', 'summary', 'description',
                  'parameters', 'request_body', 'responses', 'tags',
                  'operation_id', 'module_id', 'module_name',
                  'created_at', 'updated_at']

    def get_module_name(self, obj):
        return obj.module.name if obj.module else None


class APISpecificationSerializer(serializers.ModelSerializer):
    """API规范序列化器"""
    # 直接展示外键对象的重要字段（避免前端二次查询）
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    endpoints_count = serializers.SerializerMethodField()

    class Meta:
        model = APISpecification
        fields = [
            'id', 'project', 'project_name',
            'spec_name', 'description',
            'file_name', 'file_size', 'file_size_mb', 'file_type',
            'uploaded_file', 'spec_type',
            'status',
            'parsed_content', 'error_message',
            'created_by_username',
            'endpoints_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'uploaded_file', 'file_name', 'file_size', 'file_size_mb', 'file_type',
            'spec_type', 'status', 'parsed_content', 'error_message',
            'created_by_username', 'project_name', 'endpoints_count',
            'created_at', 'updated_at'
        ]

    def get_endpoints_count(self, obj):
        """获取端点数量"""
        return obj.endpoints.count()


class APISpecificationCreateSerializer(serializers.ModelSerializer):
    """API规范创建序列化器"""
    
    class Meta:
        model = APISpecification
        fields = ['spec_type', 'spec_name', 'description']


class APITestCaseSerializer(serializers.ModelSerializer):
    """API测试用例列表序列化器 - 用于列表页面"""
    endpoint_info = serializers.SerializerMethodField()
    last_result_info = serializers.SerializerMethodField()
    steps_count = serializers.SerializerMethodField()
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = APITestCase
        fields = [
            'id', 'title', 'description', 'test_case_type',
            'test_type', 'priority', 'endpoint_info', 'last_result_info',
            'steps_count', 'sort_order', 'created_by_username', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_steps_count(self, obj):
        """从 script_content 中读取实际步骤数量（兼容 JSON 和 YAML 两种格式）"""
        import json as _json
        import yaml as _yaml
        try:
            sc = obj.script_content
            if not sc:
                return 0
            # 先尝试 JSON 解析，失败则尝试 YAML
            if isinstance(sc, str):
                try:
                    sc = _json.loads(sc)
                except (_json.JSONDecodeError, ValueError):
                    sc = _yaml.safe_load(sc)
            if isinstance(sc, dict):
                teststeps = sc.get('teststeps', [])
                return len(teststeps) if isinstance(teststeps, list) else 0
        except Exception:
            pass
        return 0

    def get_endpoint_info(self, obj):
        """获取端点信息（仅端点测试用例）"""
        if obj.is_endpoint_test_case and obj.endpoint:
            ep = obj.endpoint
            return {
                'id': ep.id,
                'spec_id': ep.spec_id,
                'path': ep.path,
                'method': ep.method,
                'summary': ep.summary,
                'description': ep.description,
                'module_id': ep.module_id,
                'module_name': ep.module.name if ep.module else None,
            }
        return None

    def get_last_result_info(self, obj):
        """获取最新测试结果信息"""
        # 通过APITestCaseExecutionDetail获取最新的执行记录
        # 注意：这里通过APITestCaseExecutionDetail查找，它关联的是APITestCase
        try:
            # 首先尝试通过APITestCaseExecutionDetail查找
            latest_execution_detail = APITestCaseExecutionDetail.objects.filter(
                test_case__title=obj.title,  # 通过标题匹配
                test_case__project=obj.project  # 确保是同一个项目
            ).order_by('-execution__created_at').first()
            
            if latest_execution_detail:
                execution = latest_execution_detail.execution
                return {
                    'id': execution.id,
                    'status': execution.status,
                    'duration': execution.duration or 0,
                    'created_at': execution.created_at,
                    'httprunner_success': execution.status == 'passed',
                    'total_steps': 1,  # 单用例执行只有1个步骤
                    'success_steps': 1 if execution.status == 'passed' else 0,
                    'failure_steps': 1 if execution.status == 'failed' else 0,
                    'error_steps': 1 if execution.status == 'error' else 0
                }
        except Exception:
            # 如果查询失败，返回None
            pass
        
        return None

class APITestCaseDetailSerializer(serializers.ModelSerializer):
    """API测试用例详情序列化器 - 用于详情页面和编辑组件"""
    endpoint_info = serializers.SerializerMethodField()
    scenario_info = serializers.SerializerMethodField()
    last_result_info = serializers.SerializerMethodField()
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    test_case_type_display = serializers.CharField(source='get_test_case_type_display', read_only=True)
    
    class Meta:
        model = APITestCase
        fields = [
            'id', 'title', 'description', 'test_case_type', 'test_case_type_display',
            'test_type', 'timeout', 'retry_count',
            'priority', 'endpoint_info', 'scenario_info', 'last_result_info',
            'script_content', 'created_by_username', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_endpoint_info(self, obj):
        """获取端点信息（仅端点测试用例）"""
        if obj.is_endpoint_test_case and obj.endpoint:
            ep = obj.endpoint
            return {
                'id':          ep.id,
                'spec_id':     ep.spec_id,
                'path':        ep.path,
                'method':      ep.method,
                'summary':     ep.summary,
                'description': ep.description,
                'tags':        ep.tags,
                'module_id':   ep.module_id,
                'module_name': ep.module.name if ep.module else None,
            }
        return None
    
    def get_scenario_info(self, obj):
        """获取场景信息（仅场景测试用例）"""
        if obj.is_scenario_test_case:
            return {}
        return None
    
    def get_last_result_info(self, obj):
        """获取最新测试结果信息"""
        # 通过APITestCaseExecutionDetail获取最新的执行记录
        # 注意：这里通过APITestCaseExecutionDetail查找，它关联的是APITestCase
        try:
            # 首先尝试通过APITestCaseExecutionDetail查找
            latest_execution_detail = APITestCaseExecutionDetail.objects.filter(
                test_case__title=obj.title,  # 通过标题匹配
                test_case__project=obj.project  # 确保是同一个项目
            ).order_by('-execution__created_at').first()
            
            if latest_execution_detail:
                execution = latest_execution_detail.execution
                return {
                    'id': execution.id,
                    'status': execution.status,
                    'duration': execution.duration or 0,
                    'created_at': execution.created_at,
                    'httprunner_success': execution.status == 'passed',
                    'total_steps': 1,  # 单用例执行只有1个步骤
                    'success_steps': 1 if execution.status == 'passed' else 0,
                    'failure_steps': 1 if execution.status == 'failed' else 0,
                    'error_steps': 1 if execution.status == 'error' else 0
                }
        except Exception:
            # 如果查询失败，返回None
            pass
        
        return None


class APITestCaseCreateSerializer(serializers.ModelSerializer):
    """API测试用例创建序列化器"""
    class Meta:
        model = APITestCase
        fields = [
            'title', 'description', 'test_case_type', 'endpoint', 'test_type',
            'timeout', 'retry_count', 'priority', 'script_content',
        ]
    
    def validate(self, data):
        """验证数据"""
        test_case_type = data.get('test_case_type', 'endpoint')
        endpoint = data.get('endpoint')
        test_type = data.get('test_type')
        
        # 端点测试用例验证
        if test_case_type == 'endpoint':
            if not endpoint:
                raise serializers.ValidationError("端点测试用例必须关联一个API端点")
            if not test_type:
                raise serializers.ValidationError("端点测试用例必须指定测试类型")
        
        # 场景测试用例验证
        elif test_case_type == 'scenario':
            if endpoint:
                raise serializers.ValidationError("场景测试用例不能关联API端点")
            if test_type:
                raise serializers.ValidationError("场景测试用例不需要指定测试类型")
        
        # 设置默认值
        if test_case_type == 'endpoint':
            if 'test_type' not in data:
                data['test_type'] = 'positive'
        
        return data


# 新增：AI测试生成相关序列化器
class EndpointTestGenerationSerializer(serializers.Serializer):
    """端点测试生成序列化器"""
    # 新的测试类型配置格式：支持为每种类型设置不同的数量
    test_type_configs = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=10),
        required=False,
        default={
            'positive': 3,
            'negative': 3,
            'boundary': 3,
            'security': 3
        },
        help_text="测试类型配置，格式：{'positive': 3, 'negative': 2, 'boundary': 1, 'security': 1}"
    )
    
    # 向后兼容：保留旧的参数格式
    case_types = serializers.ListField(
        child=serializers.ChoiceField(choices=[
            ('positive', 'Positive Test'),
            ('negative', 'Negative Test'),
            ('boundary', 'Boundary Test'),
            ('security', 'Security Test')
        ]),
        required=False,
        help_text="已废弃，请使用test_type_configs"
    )
    
    test_count_per_type = serializers.IntegerField(required=False, min_value=1, max_value=10, help_text="已废弃，请使用test_type_configs")
    
    # 可选的自定义配置
    custom_prompt = serializers.CharField(required=False, allow_blank=True, help_text="自定义AI提示词")
    include_assertions = serializers.BooleanField(required=False, default=True, help_text="是否包含断言")
    include_negative_cases = serializers.BooleanField(required=False, default=True, help_text="是否包含负面测试用例")
    
    def validate(self, data):
        """验证测试类型配置"""
        # 优先使用新的配置格式
        if 'test_type_configs' in data and data['test_type_configs']:
            # 验证至少有一种测试类型
            if not data['test_type_configs']:
                raise serializers.ValidationError("至少需要配置一种测试类型")
            
            # 验证所有数量都在有效范围内
            for test_type, count in data['test_type_configs'].items():
                if count < 1 or count > 10:
                    raise serializers.ValidationError(f"测试类型 {test_type} 的数量必须在1-10之间")
            
            return data
        
        # 向后兼容：如果没有新配置，使用旧配置
        if 'case_types' in data and data['case_types']:
            case_types = data['case_types']
            test_count = data.get('test_count_per_type', 3)
            
            # 转换为新格式
            data['test_type_configs'] = {}
            for case_type in case_types:
                data['test_type_configs'][case_type] = test_count
            
            return data
        
        # 如果都没有，使用默认配置
        data['test_type_configs'] = {
            'positive': 3,
            'negative': 3,
            'boundary': 3,
            'security': 3
        }
        
        return data





















class ScenarioGenerationRequestSerializer(serializers.Serializer):
    """智能场景生成请求序列化器"""
    user_request = serializers.CharField(
        help_text='用户场景描述',
        required=True,
        max_length=1000
    )
    
    def validate_user_request(self, value):
        """验证用户请求"""
        if not value.strip():
            raise serializers.ValidationError("场景描述不能为空")
        if len(value.strip()) < 10:
            raise serializers.ValidationError("场景描述至少需要10个字符")
        return value.strip()

class ScenarioGenerationResponseSerializer(serializers.Serializer):
    """智能场景生成响应序列化器"""
    success = serializers.BooleanField(help_text='是否成功')
    message = serializers.CharField(help_text='响应消息')
    test_case_id = serializers.IntegerField(help_text='创建的测试用例ID')
    generated_script = serializers.CharField(help_text='生成的测试脚本')

class EndpointTestGenerationStatusResponseSerializer(serializers.Serializer):
    """端点测试用例生成状态响应序列化器（优化版）"""
    
    class EndpointInfoSerializer(serializers.Serializer):
        """端点信息序列化器"""
        spec_id = serializers.IntegerField(help_text='API规范ID')
        endpoint_id = serializers.IntegerField(help_text='端点ID')
        path = serializers.CharField(help_text='端点路径')
        method = serializers.CharField(help_text='HTTP方法')
    
    class TestCasesSummarySerializer(serializers.Serializer):
        """测试用例摘要序列化器"""
        total_cases = serializers.IntegerField(help_text='总用例数')
        created_cases_count = serializers.IntegerField(help_text='已创建用例数')
        
        class CaseTypesSerializer(serializers.Serializer):
            """用例类型统计序列化器"""
            positive = serializers.IntegerField(help_text='正向用例数量')
            negative = serializers.IntegerField(help_text='负向用例数量')
            boundary = serializers.IntegerField(help_text='边界用例数量')
            security = serializers.IntegerField(help_text='安全用例数量')
        
        case_types = CaseTypesSerializer(help_text='各类型用例数量统计')
    
    class GenerationInfoSerializer(serializers.Serializer):
        """生成信息序列化器"""
        method = serializers.CharField(help_text='生成方法')
        model = serializers.CharField(help_text='使用的模型')
        model_type = serializers.CharField(help_text='模型类型')
    
    # 基础字段
    task_id = serializers.CharField(help_text='任务ID')
    status = serializers.CharField(help_text='任务状态')
    progress = serializers.IntegerField(help_text='进度百分比')
    message = serializers.CharField(help_text='状态消息')
    
    # 完成状态特有字段
    endpoint_info = EndpointInfoSerializer(help_text='端点信息', required=False)
    test_cases_summary = TestCasesSummarySerializer(help_text='测试用例摘要', required=False)
    generation_info = GenerationInfoSerializer(help_text='生成信息', required=False)
    full_script = serializers.CharField(help_text='完整的JSON格式测试脚本', required=False)
    completed_at = serializers.DateTimeField(help_text='完成时间', required=False)
    
    # 失败状态特有字段
    error = serializers.CharField(help_text='错误信息', required=False)
    error_details = serializers.CharField(help_text='详细错误信息', required=False)
    
    # 备用字段（当结果结构不符合预期时）
    raw_result = serializers.DictField(help_text='原始结果数据', required=False)


class ScenarioGenerationStatusResponseSerializer(serializers.Serializer):
    """智能场景生成状态响应序列化器（优化版）"""
    
    class ScenarioInfoSerializer(serializers.Serializer):
        """场景信息序列化器"""
        test_case_id = serializers.IntegerField(help_text='测试用例ID')
        scenario_name = serializers.CharField(help_text='场景名称')
        scenario_type = serializers.CharField(help_text='场景类型')
    
    class GenerationSummarySerializer(serializers.Serializer):
        """生成摘要序列化器（简化版：移除business_steps_count）"""
        mapped_apis_count = serializers.IntegerField(help_text='映射的API数量')
        api_specs_count = serializers.IntegerField(help_text='API规范数量')
        has_script = serializers.BooleanField(help_text='是否生成了测试脚本')
    
    class ContentDetailsSerializer(serializers.Serializer):
        """内容详情序列化器（简化版：移除scenario_plan和business_steps）"""
        mapped_apis = serializers.ListField(help_text='映射的API列表')
        api_specifications = serializers.ListField(help_text='API规范列表')
    
    # 基础字段
    task_id = serializers.CharField(help_text='任务ID')
    status = serializers.CharField(help_text='任务状态')
    progress = serializers.IntegerField(help_text='进度百分比')
    message = serializers.CharField(help_text='状态消息')
    
    # 完成状态特有字段
    scenario_info = ScenarioInfoSerializer(help_text='场景信息', required=False)
    generation_summary = GenerationSummarySerializer(help_text='生成摘要', required=False)
    content_details = ContentDetailsSerializer(help_text='内容详情', required=False)
    generated_script = serializers.CharField(help_text='生成的测试脚本', required=False)
    completed_at = serializers.CharField(help_text='完成时间', required=False)
    
    # 失败状态特有字段
    error = serializers.CharField(help_text='错误信息', required=False)
    
    # 其他状态字段
    note = serializers.CharField(help_text='提示信息', required=False)
    
    # 备用字段（当结果结构不符合预期时）
    raw_result = serializers.DictField(help_text='原始结果数据', required=False)


class EndpointTestCasesResponseSerializer(serializers.Serializer):
    """端点测试用例列表响应序列化器（优化版）"""
    
    class EndpointSummarySerializer(serializers.Serializer):
        """端点摘要序列化器"""
        path = serializers.CharField(help_text='端点路径')
        method = serializers.CharField(help_text='HTTP方法')
        summary = serializers.CharField(help_text='端点描述')
    
    class TestCaseSerializer(serializers.Serializer):
        """测试用例序列化器"""
        id = serializers.IntegerField(help_text='测试用例ID')
        title = serializers.CharField(help_text='测试用例标题')
        description = serializers.CharField(help_text='测试用例描述')
        test_case_type = serializers.CharField(help_text='测试用例类型')
        test_case_type_display = serializers.CharField(help_text='测试用例类型显示名称')
        test_type = serializers.CharField(help_text='测试类型（positive/negative/boundary/security）')
        request_data = serializers.DictField(help_text='请求数据')
        expected_response = serializers.DictField(help_text='期望响应')
        variables = serializers.DictField(help_text='变量数据')
        expected_status_code = serializers.IntegerField(help_text='期望状态码')
        assertions = serializers.ListField(help_text='断言列表')
        timeout = serializers.IntegerField(help_text='超时时间')
        retry_count = serializers.IntegerField(help_text='重试次数')
        priority = serializers.CharField(help_text='优先级')
        endpoint_info = serializers.DictField(help_text='端点信息')
        scenario_info = serializers.CharField(help_text='场景信息', allow_null=True)
        script_content = serializers.CharField(help_text='脚本内容')
        created_by_username = serializers.CharField(help_text='创建者用户名')
        created_at = serializers.DateTimeField(help_text='创建时间')
        updated_at = serializers.DateTimeField(help_text='更新时间')
    
    # 响应数据字段
    test_cases = TestCaseSerializer(many=True, help_text='测试用例列表')
    total_count = serializers.IntegerField(help_text='测试用例总数')
    endpoint_summary = EndpointSummarySerializer(help_text='端点摘要信息')


class CeleryTaskStatusResponseSerializer(serializers.Serializer):
    """Celery任务状态响应序列化器（统一版）"""
    
    # 基础字段
    task_id = serializers.CharField(help_text='任务ID')
    status = serializers.ChoiceField(
        choices=[
            ('PROCESSING', '处理中'),
            ('COMPLETED', '已完成'),
            ('FAILED', '失败')
        ],
        help_text='任务状态'
    )
    progress = serializers.IntegerField(help_text='进度百分比（0-100）')
    task_message = serializers.CharField(help_text='任务状态消息')
    

    
    # 失败状态特有字段
    error = serializers.CharField(help_text='错误信息', required=False)
    error_details = serializers.CharField(help_text='详细错误信息', required=False)
    
    # 完成状态特有字段（端点测试用例生成）
    endpoint_info = serializers.DictField(help_text='端点信息', required=False)
    test_cases_summary = serializers.DictField(help_text='测试用例摘要', required=False)
    generation_info = serializers.DictField(help_text='生成信息', required=False)
    full_yaml = serializers.CharField(help_text='完整YAML脚本', required=False)
    completed_at = serializers.CharField(help_text='完成时间', required=False)
    
    # 完成状态特有字段（智能场景生成）
    scenario_info = serializers.DictField(help_text='场景信息', required=False)
    generation_summary = serializers.DictField(help_text='生成摘要', required=False)
    content_details = serializers.DictField(help_text='内容详情', required=False)
    generated_script = serializers.CharField(help_text='生成的测试脚本', required=False)
    
# ============ API测试套件序列化器 ============

class APITestSuiteSerializer(serializers.ModelSerializer):
    """API测试套件序列化器"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    test_cases_count = serializers.IntegerField(read_only=True)
    active_test_cases_count = serializers.IntegerField(read_only=True)
    test_cases = serializers.SerializerMethodField()
    
    class Meta:
        model = APITestSuite
        fields = [
            # 基本信息
            'id', 'name', 'description',
            # 关联信息
            'user', 'user_name', 'project', 'project_name',
            # 套件属性
            'status', 'status_display', 'tags',
            # 统计信息
            'test_cases_count', 'active_test_cases_count', 'test_cases',
            # 时间信息
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at'
        ]
    
    def get_test_cases(self, obj):
        """获取测试用例列表"""
        test_cases = obj.test_cases.all()
        return APITestCaseSerializer(test_cases, many=True).data


class APITestSuiteCreateSerializer(serializers.ModelSerializer):
    """API测试套件创建序列化器"""
    
    class Meta:
        model = APITestSuite
        fields = [
            'name', 'description', 'status', 'tags'
        ]
        extra_kwargs = {
            'name': {'required': True},
            'description': {'required': False, 'allow_blank': True},
            'status': {'required': False, 'default': 'active'},
            'tags': {'required': False, 'default': list}
        }
    
    def create(self, validated_data):
        # 自动设置用户和项目（由视图提供）
        validated_data['user'] = self.context['request'].user
        validated_data['project'] = self.context['project']
        return super().create(validated_data)


class APITestSuiteUpdateSerializer(serializers.ModelSerializer):
    """API测试套件更新序列化器（含 test_case_order 用于拖拽排序）"""
    
    class Meta:
        model = APITestSuite
        fields = [
            'name', 'description', 'status', 'tags', 'test_case_order'
        ]
        extra_kwargs = {
            'name': {'required': False},
            'description': {'required': False, 'allow_blank': True},
            'status': {'required': False},
            'tags': {'required': False},
            'test_case_order': {'required': False},
        }


class APITestSuiteAddTestCaseSerializer(serializers.Serializer):
    """API测试套件添加测试用例序列化器"""
    test_case_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="测试用例ID列表"
    )
    order = serializers.IntegerField(required=False, help_text="执行顺序")
    
    def validate_test_case_ids(self, value):
        """验证测试用例ID列表"""
        if not value:
            raise serializers.ValidationError("测试用例ID列表不能为空")
        
        # 验证测试用例是否存在且属于当前用户
        user = self.context['request'].user
        existing_cases = APITestCase.objects.filter(
            id__in=value, 
            created_by=user
        ).values_list('id', flat=True)
        
        missing_cases = set(value) - set(existing_cases)
        if missing_cases:
            raise serializers.ValidationError(f"测试用例不存在或无权限访问: {list(missing_cases)}")
        
        return value


# ============ API测试执行记录序列化器 ============

class APITestExecutionListSerializer(serializers.ModelSerializer):
    """API测试执行记录列表序列化器"""
    executor_name = serializers.CharField(source='executor.username', read_only=True)
    environment_name = serializers.CharField(source='environment.name', read_only=True)
    exec_type_display = serializers.CharField(source='get_exec_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    trigger_type_display = serializers.CharField(source='get_trigger_type_display', read_only=True)
    pass_rate = serializers.FloatField(read_only=True)
    
    class Meta:
        model = APITestExecution
        fields = [
            'id', 'exec_type', 'exec_type_display', 'name', 'description', 'status', 'status_display',
            'trigger_type', 'trigger_type_display', 'executor', 'executor_name',
            'environment', 'environment_name', 'task_id',
            'start_time', 'end_time', 'duration', 'pass_rate',
            'log_path', 'report_path', 'created_at', 'updated_at'
        ]


class APITestCaseExecutionDetailSerializer(serializers.ModelSerializer):
    """单用例执行详情序列化器 - 用于单用例执行详情页面"""
    test_case_title = serializers.CharField(source='test_case.title', read_only=True)
    test_case_description = serializers.CharField(source='test_case.description', read_only=True)
    environment_name = serializers.CharField(source='execution.environment.name', read_only=True)
    environment_base_url = serializers.SerializerMethodField()
    httprunner_result = serializers.SerializerMethodField()
    
    class Meta:
        model = APITestCaseExecutionDetail
        fields = [
            'id', 'execution', 'test_case', 'test_case_title', 'test_case_description',
            'name', 'status', 'environment_name', 'environment_base_url',
            'start_time', 'end_time', 'duration',
            'error_message', 'log', 'httprunner_result'
        ]
        read_only_fields = ['id', 'execution']
    
    def get_environment_base_url(self, obj):
        """获取环境base_url"""
        if obj.execution.environment:
            web_config = obj.execution.environment.get_api_config()
            return web_config.get('base_url', '') if web_config else ''
        return ''
    
    def get_httprunner_result(self, obj):
        """解析HttpRunner结果为JSON对象"""
        if obj.httprunner_result:
            try:
                import json
                return json.loads(obj.httprunner_result)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}


class APITestSuiteExecutionDetailSerializer(serializers.ModelSerializer):
    """API测试套件执行详情序列化器 - 用于套件执行详情页面"""
    test_suite_name = serializers.CharField(source='test_suite.name', read_only=True)
    environment_name = serializers.CharField(source='execution.environment.name', read_only=True)
    environment_base_url = serializers.SerializerMethodField()
    pass_rate = serializers.FloatField(read_only=True)
    allure_report_url = serializers.SerializerMethodField()
    
    class Meta:
        model = APITestSuiteExecutionDetail
        fields = [
            'id', 'execution', 'test_suite', 'test_suite_name',
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases',
            'pass_rate', 'environment_name', 'environment_base_url',
            'start_time', 'end_time', 'duration', 'log', 'allure_report', 'allure_report_url'
        ]
        read_only_fields = ['id', 'execution']
    
    def get_environment_base_url(self, obj):
        """获取环境base_url"""
        if obj.execution.environment:
            api_config = obj.execution.environment.get_api_config()
            return api_config.get('base_url', '') if api_config else ''
        return ''
    
    def get_allure_report_url(self, obj):
        """生成Allure报告访问URL - 返回静态文件URL"""
        if obj.allure_report:
            import os
            if not os.path.exists(obj.allure_report):
                return None

            from django.conf import settings
            report_dir = os.path.dirname(obj.allure_report)
            report_filename = os.path.basename(obj.allure_report)

            # 使用HTTPRUNNER_REPORTS_ROOT计算相对路径
            relative_path = os.path.relpath(report_dir, settings.HTTPRUNNER_REPORTS_ROOT)
            static_url = f"/httprunner-reports/{relative_path}/{report_filename}"

            return static_url
        return None


class APITestSuiteCaseExecutionSerializer(serializers.ModelSerializer):
    """套件用例执行明细序列化器"""
    test_case_title = serializers.CharField(source='test_case.title', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    httprunner_result = serializers.SerializerMethodField()
    
    class Meta:
        model = APITestSuiteCaseExecution
        fields = [
            'id', 'suite_execution', 'test_case', 'test_case_title',
            'name', 'status', 'status_display', 'duration',
            'error_message', 'log', 'httprunner_result'
        ]
        read_only_fields = ['id', 'suite_execution']
    
    def get_httprunner_result(self, obj):
        """解析HttpRunner结果为JSON对象"""
        if obj.httprunner_result:
            try:
                import json
                return json.loads(obj.httprunner_result)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}


# ============================================================
# 端点测试用例脚本更新序列化器（精简版，仅接受必要字段）
# ============================================================
class APITestCaseScriptUpdateSerializer(serializers.ModelSerializer):
    """
    用于 PUT /test-cases/{id}/ 的精简序列化器。
    - 只接受安全的元数据字段 + script_content
    - script_content 接受字符串或字典，统一序列化为 JSON 字符串存入 TextField
    - 拒绝 pre_script / post_script / request_data / variables / assertions 等历史冗余字段
    """

    script_content = serializers.JSONField(required=False, allow_null=True, default=None)

    class Meta:
        model = APITestCase
        fields = [
            'title',
            'description',
            'test_type',
            'priority',
            'timeout',
            'retry_count',
            'script_content',
        ]

    def to_internal_value(self, data):
        import json
        value = super().to_internal_value(data)
        sc = value.get('script_content')
        if sc is not None:
            if isinstance(sc, (dict, list)):
                # 将 dict/list 序列化为 JSON 字符串，适配 TextField 存储
                value['script_content'] = json.dumps(sc, ensure_ascii=False)
            elif isinstance(sc, str):
                # 验证字符串是否是合法 JSON
                try:
                    json.loads(sc)
                except json.JSONDecodeError:
                    raise serializers.ValidationError(
                        {'script_content': 'script_content 必须是合法的 JSON 字符串或 JSON 对象'}
                    )
        return value

