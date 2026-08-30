"""
Web Testing Serializers
用于Web UI自动化测试的序列化器
"""
from rest_framework import serializers
from projects.models import Environment
from ai_core.models import LLMConfiguration, ModelType
from .models import (
    WebUITestCase, WebUITestExecution, WebUITestSuite, WebUITestModule,
    WebUITestCaseExecutionDetail, WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution,
    WebPage, WebElement, WebUIScriptGeneration
)
from .generation_repository import create_generation
from .generation_preflight import exploration_requires_write_confirmation
from .generation_save_state import is_generation_saved
from .generation_security import (
    GenerationInputSecurityError,
    build_safe_target_url,
    clear_temporary_credentials,
    find_suspected_credentials,
    normalize_start_path,
    redact_text,
    store_temporary_credentials,
    validate_temporary_credentials,
)
from .script_contract import ScriptContractError, normalize_for_storage, store_script_content
from .execution_diagnostics import safe_screenshot_relative_path


class WebPageSerializer(serializers.ModelSerializer):
    """Web页面 (POM) 序列化器"""
    module_id = serializers.PrimaryKeyRelatedField(
        queryset=WebUITestModule.objects.all(),
        required=False,
        allow_null=True,
        source='module',
    )
    module_name = serializers.SerializerMethodField()

    class Meta:
        model = WebPage
        fields = ['id', 'project', 'module_id', 'module_name', 'name', 'url_path', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'project': {'required': False}}

    def get_module_name(self, obj):
        return obj.module.name if obj.module else None


class WebElementSerializer(serializers.ModelSerializer):
    """Web元素 (POM) 序列化器"""
    class Meta:
        model = WebElement
        fields = ['id', 'page', 'name', 'locator_type', 'locator_value', 'action_type', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class WebUIScriptGenerationSerializer(serializers.ModelSerializer):
    """Safe, persistent view of one WebUI script-generation task."""

    environment_id = serializers.IntegerField(read_only=True)
    environment_name = serializers.CharField(source='environment.name', read_only=True)
    test_case_id = serializers.IntegerField(read_only=True, allow_null=True)
    is_saved = serializers.SerializerMethodField()

    def get_is_saved(self, obj):
        return is_generation_saved(obj)

    class Meta:
        model = WebUIScriptGeneration
        fields = [
            'id', 'project', 'user', 'environment_id', 'environment_name', 'test_case_id', 'is_saved',
            'source_mode', 'celery_task_id', 'status', 'current_stage', 'progress',
            'start_path', 'target_url_safe', 'description_safe', 'scenario_spec',
            'exploration_snapshot', 'script_draft', 'quality_report', 'warnings',
            'model_info', 'tool_stats', 'repair_count', 'credentials_required',
            'revision', 'resume_count', 'clarifications',
            'credentials_provided', 'credentials_expired', 'error_code', 'error_message',
            'cancel_requested_at', 'started_at', 'completed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'project', 'user', 'environment_id', 'environment_name', 'test_case_id', 'is_saved',
            'source_mode', 'celery_task_id', 'status', 'current_stage', 'progress',
            'start_path', 'target_url_safe', 'description_safe', 'scenario_spec',
            'exploration_snapshot', 'script_draft', 'quality_report', 'warnings',
            'model_info', 'tool_stats', 'repair_count', 'credentials_required',
            'revision', 'resume_count', 'clarifications',
            'credentials_provided', 'credentials_expired', 'error_code', 'error_message',
            'cancel_requested_at', 'started_at', 'completed_at', 'created_at', 'updated_at',
        ]


class WebUIScriptGenerationCreateSerializer(serializers.Serializer):
    """Validate a new V2 generation request without persisting secret values."""

    description = serializers.CharField(max_length=2000, trim_whitespace=True)
    environment_id = serializers.IntegerField(min_value=1)
    start_path = serializers.CharField(max_length=500, required=False, allow_blank=False)
    url = serializers.CharField(max_length=1000, required=False, allow_blank=False, write_only=True)
    source_mode = serializers.ChoiceField(
        choices=WebUIScriptGeneration.SourceMode.choices,
        default=WebUIScriptGeneration.SourceMode.MANUAL_PROMPT,
    )
    test_case_id = serializers.IntegerField(min_value=1, required=False)
    model_config_id = serializers.IntegerField(min_value=1, required=False, write_only=True)
    temporary_credentials = serializers.DictField(required=False, write_only=True)

    def validate_temporary_credentials(self, value):
        try:
            return validate_temporary_credentials(value)
        except GenerationInputSecurityError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        project = self.context['project']
        description = attrs['description']
        findings = find_suspected_credentials(description)
        if findings:
            raise serializers.ValidationError({
                'description': '场景描述中疑似包含账号或密码，请改用 temporary_credentials 单独提交。'
            })

        supplied_start = attrs.get('start_path')
        supplied_url = attrs.get('url')
        if supplied_start and supplied_url and supplied_start != supplied_url:
            raise serializers.ValidationError('start_path 与 url 不能同时传入不同值')
        raw_target = supplied_start or supplied_url

        try:
            environment = project.environments.get(pk=attrs['environment_id'])
        except Environment.DoesNotExist as exc:
            raise serializers.ValidationError({'environment_id': 'WebUI 环境必须属于当前项目'}) from exc
        if not environment.is_web_environment:
            raise serializers.ValidationError({'environment_id': '请选择 WebUI 类型的环境'})
        if not environment.is_active:
            raise serializers.ValidationError({'environment_id': '所选 WebUI 环境已停用'})
        base_url = (environment.config or {}).get('base_url', '')
        try:
            start_path = normalize_start_path(raw_target, base_url)
        except GenerationInputSecurityError as exc:
            raise serializers.ValidationError({'start_path': str(exc)}) from exc

        test_case = None
        test_case_id = attrs.get('test_case_id')
        if test_case_id is not None:
            try:
                test_case = WebUITestCase.objects.get(pk=test_case_id, project=project)
            except WebUITestCase.DoesNotExist as exc:
                raise serializers.ValidationError({'test_case_id': '测试用例必须属于当前项目'}) from exc
        if attrs['source_mode'] == WebUIScriptGeneration.SourceMode.TEST_CASE and test_case is None:
            raise serializers.ValidationError({'test_case_id': '测试用例入口必须提供 test_case_id'})

        requested_model_id = attrs.get('model_config_id')
        model_query = LLMConfiguration.objects.filter(model_type=ModelType.LLM, is_active=True)
        model_config = (
            model_query.filter(pk=requested_model_id).first()
            if requested_model_id is not None
            else model_query.order_by('-created_at').first()
        )
        if model_config is None:
            field = 'model_config_id' if requested_model_id is not None else 'non_field_errors'
            raise serializers.ValidationError({field: '没有可用的启用 LLM 配置'})

        attrs['environment'] = environment
        attrs['test_case'] = test_case
        attrs['normalized_start_path'] = start_path
        attrs['base_url'] = base_url
        attrs['model_config'] = model_config
        return attrs

    def create(self, validated_data):
        project = self.context['project']
        user = self.context['request'].user
        credentials = validated_data.pop('temporary_credentials', None)
        environment = validated_data.pop('environment')
        test_case = validated_data.pop('test_case')
        start_path = validated_data.pop('normalized_start_path')
        base_url = validated_data.pop('base_url')
        model_config = validated_data.pop('model_config')
        validated_data.pop('environment_id', None)
        validated_data.pop('test_case_id', None)
        validated_data.pop('url', None)
        validated_data.pop('model_config_id', None)
        description = validated_data.pop('description')

        generation = None
        try:
            generation = create_generation(
                project=project,
                user=user,
                environment=environment,
                test_case=test_case,
                source_mode=validated_data['source_mode'],
                start_path=start_path,
                target_url_safe=build_safe_target_url(base_url, start_path),
                description_safe=redact_text(description),
                credentials_provided=credentials is not None,
                model_info={
                    'config_id': model_config.id,
                    'provider': model_config.provider,
                    'model_name': model_config.model_name,
                },
            )
            if credentials is not None:
                store_temporary_credentials(generation.pk, credentials)
            return generation
        except Exception:
            if generation is not None:
                clear_temporary_credentials(generation.pk)
                generation.delete()
            raise


class WebUIScriptGenerationSaveSerializer(serializers.Serializer):
    """Optional user-visible title for saving a quality-approved V2 draft."""

    title = serializers.CharField(max_length=200, required=False, allow_blank=False, trim_whitespace=True)

    def validate_title(self, value):
        if find_suspected_credentials(value):
            raise serializers.ValidationError('标题不能包含账号、密码或密钥。')
        return redact_text(value)


class WebUIScriptGenerationClarificationAnswerSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=500, trim_whitespace=True)
    answer = serializers.CharField(max_length=1000, trim_whitespace=True)


class WebUIScriptGenerationResolveSerializer(serializers.Serializer):
    """Validate one user response to a paused generation without storing secrets."""

    expected_status = serializers.ChoiceField(choices=[
        WebUIScriptGeneration.Status.NEEDS_INPUT,
        WebUIScriptGeneration.Status.NEEDS_CONFIRMATION,
        WebUIScriptGeneration.Status.NEEDS_CREDENTIALS,
    ])
    expected_revision = serializers.IntegerField(min_value=0)
    description = serializers.CharField(
        max_length=2000,
        required=False,
        allow_blank=False,
        trim_whitespace=True,
    )
    clarification_answers = WebUIScriptGenerationClarificationAnswerSerializer(
        many=True,
        required=False,
    )
    temporary_credentials = serializers.DictField(required=False, write_only=True)

    def validate_temporary_credentials(self, value):
        try:
            return validate_temporary_credentials(value)
        except GenerationInputSecurityError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    @staticmethod
    def _questions(generation):
        scenario_questions = (generation.scenario_spec or {}).get('ambiguities') or []
        questions = [str(item).strip() for item in (scenario_questions or generation.warnings or []) if str(item).strip()]
        return questions or ['请补充当前场景中无法安全确定的内容。']

    def validate(self, attrs):
        generation = self.context['generation']
        description = attrs.get('description')
        answers = attrs.get('clarification_answers') or []
        credentials = attrs.get('temporary_credentials')

        if description and find_suspected_credentials(description):
            raise serializers.ValidationError({
                'description': '修订描述中疑似包含账号或密码，请改用 temporary_credentials 单独提交。'
            })
        for index, item in enumerate(answers):
            if find_suspected_credentials(item['question']) or find_suspected_credentials(item['answer']):
                raise serializers.ValidationError({
                    'clarification_answers': f'第 {index + 1} 项疑似包含账号、密码或密钥。'
                })

        if generation.status == WebUIScriptGeneration.Status.NEEDS_INPUT:
            if not description:
                raise serializers.ValidationError({'description': '请补充完整的测试描述。'})
        elif generation.status == WebUIScriptGeneration.Status.NEEDS_CREDENTIALS:
            if not credentials:
                raise serializers.ValidationError({'temporary_credentials': '请提供本次探索登录信息。'})
        elif generation.status == WebUIScriptGeneration.Status.NEEDS_CONFIRMATION:
            if generation.error_code == 'INPUT_AMBIGUOUS':
                expected_questions = self._questions(generation)
                submitted = {item['question']: item['answer'] for item in answers}
                if len(submitted) != len(answers):
                    raise serializers.ValidationError({'clarification_answers': '待确认项不能重复。'})
                if set(submitted) != set(expected_questions):
                    raise serializers.ValidationError({'clarification_answers': '请逐项回答当前全部待确认问题。'})
            else:
                if not description:
                    raise serializers.ValidationError({'description': '请修订测试描述后继续。'})
                if generation.error_code == 'EXPLORATION_WRITE_CONFIRMATION_REQUIRED' and exploration_requires_write_confirmation(description):
                    raise serializers.ValidationError({
                        'description': '探索阶段必须保持只读，请移除提交、新增、编辑或删除要求。'
                    })

        attrs['safe_description'] = redact_text(description) if description else None
        attrs['safe_answers'] = [
            {'question': redact_text(item['question']), 'answer': redact_text(item['answer'])}
            for item in answers
        ]
        return attrs


class WebUITestModuleSerializer(serializers.ModelSerializer):
    """WebUI测试模块序列化器 - 支持树状结构"""
    children = serializers.SerializerMethodField()

    class Meta:
        model = WebUITestModule
        fields = ['id', 'name', 'parent', 'order', 'project', 'children', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'project': {'required': False}}

    def get_children(self, obj):
        """递归获取子模块"""
        children = obj.children.all().order_by('order', 'id')
        return WebUITestModuleSerializer(children, many=True).data


class WebUITestCaseSerializer(serializers.ModelSerializer):
    """WebUI测试用例列表序列化器 - 用于列表页面"""
    created_by_username = serializers.CharField(source='user.username', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    module_id = serializers.PrimaryKeyRelatedField(
        queryset=WebUITestModule.objects.all(), required=False, allow_null=True, source='module'
    )
    has_script = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = WebUITestCase
        fields = [
            # 基本信息
            'id', 'title', 'description', 'expected_result', 'created_by_username',
            # 测试属性
            'priority', 'priority_display', 'category', 'category_display',
            # 模块与脚本
            'module_id', 'has_script',
            'script_source', 'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
            # 时间信息
            'created_at', 'updated_at',
            # 执行状态记录
            'last_execute_status', 'last_execute_time', 'last_error_message',
        ]
        read_only_fields = [
            'id', 'created_by_username', 'created_at', 'updated_at',
            'last_execute_status', 'last_execute_time', 'last_error_message',
            'script_source', 'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
        ]


class WebUITestCaseDetailSerializer(serializers.ModelSerializer):
    """WebUI测试用例详情序列化器 - 用于详情页面和编辑组件"""
    created_by_username = serializers.CharField(source='user.username', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    module_id = serializers.PrimaryKeyRelatedField(
        queryset=WebUITestModule.objects.all(), required=False, allow_null=True, source='module'
    )
    has_script = serializers.BooleanField(read_only=True)

    def validate_test_script_content(self, value):
        if value in (None, ''):
            return value
        try:
            return normalize_for_storage(value)
        except ScriptContractError as exc:
            raise serializers.ValidationError(str(exc))
    
    class Meta:
        model = WebUITestCase
        fields = [
            # 基本信息
            'id', 'title', 'description', 'url', 'created_by_username',
            # 测试属性
            'priority', 'priority_display', 'category', 'category_display',
            # 模块与脚本
            'module_id', 'has_script',
            # 测试内容
            'preconditions', 'steps', 'expected_result',
            # 脚本信息
            'test_script_content', 'script_source', 'script_status', 'script_framework',
            'script_version', 'script_validation_error', 'generation_metadata',
            # 时间信息
            'created_at', 'updated_at',
            # 执行状态记录
            'last_execute_status', 'last_execute_time', 'last_error_message',
        ]
        read_only_fields = [
            'id', 'created_by_username', 'created_at', 'updated_at',
            'last_execute_status', 'last_execute_time', 'last_error_message',
            'script_source', 'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
        ]

    def update(self, instance, validated_data):
        has_script_content = 'test_script_content' in validated_data
        script_content = validated_data.pop('test_script_content', None)
        instance = super().update(instance, validated_data)
        if has_script_content:
            try:
                store_script_content(
                    instance,
                    script_content,
                    source='manual',
                )
            except ScriptContractError as exc:
                raise serializers.ValidationError({'test_script_content': str(exc)})
        return instance


class WebUITestCaseCreateSerializer(serializers.ModelSerializer):
    """WebUI测试用例创建序列化器"""
    
    class Meta:
        model = WebUITestCase
        fields = [
            # 基本信息
            'title', 'description', 'url',
            # 关联信息
            'project', 'module',
            # 测试属性
            'priority', 'category',
            # 测试内容
            'preconditions', 'steps', 'expected_result',
            # 脚本信息
            'test_script_content', 'script_source', 'script_status', 'script_framework',
            'script_version', 'script_validation_error', 'generation_metadata'
        ]
        extra_kwargs = {
            'title': {'required': True},
            'description': {'required': True},
            'expected_result': {'required': True},
            'url': {'required': False, 'allow_blank': True},
            'project': {'required': True},
            'priority': {'required': False, 'default': 'medium'},
            'category': {'required': False, 'default': 'functional'},
            'preconditions': {'required': False, 'default': list},
            'steps': {'required': False, 'default': list},
        }
        read_only_fields = [
            'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
        ]
    
    def create(self, validated_data):
        # 自动设置用户
        validated_data['user'] = self.context['request'].user
        script_content = validated_data.pop('test_script_content', None)
        script_source = validated_data.pop('script_source', 'manual')
        test_case = super().create(validated_data)
        try:
            return store_script_content(test_case, script_content, source=script_source)
        except ScriptContractError as exc:
            test_case.delete()
            raise serializers.ValidationError({'test_script_content': str(exc)})

    def validate_test_script_content(self, value):
        if value in (None, ''):
            return value
        try:
            return normalize_for_storage(value)
        except ScriptContractError as exc:
            raise serializers.ValidationError(str(exc))


class WebUITestExecutionListSerializer(serializers.ModelSerializer):
    """WebUI测试执行列表序列化器 - 用于列表页面"""
    executor_name = serializers.CharField(source='executor.username', read_only=True)
    environment_name = serializers.CharField(source='environment.name', read_only=True)
    pass_rate = serializers.FloatField(read_only=True)
    execution_duration = serializers.FloatField(read_only=True)
    project_id = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = WebUITestExecution
        fields = [
            'id', 'exec_type', 'name', 'description',
            'status', 'error_message', 'trigger_type',
            'executor_name', 'environment_name',
            'project_id',
            'browser', 'task_id', 'start_time', 'end_time', 'duration',
            'log_path', 'report_path', 'pass_rate', 'execution_duration',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'executor', 'project_id', 'created_at', 'updated_at'
        ]


class WebUITestSuiteExecutionDetailSerializer(serializers.ModelSerializer):
    """WebUI测试套件执行详情序列化器 - 用于套件执行详情页面"""
    test_suite_name = serializers.CharField(source='test_suite.name', read_only=True)
    browser = serializers.CharField(source='execution.browser', read_only=True)
    environment_name = serializers.CharField(source='execution.environment.name', read_only=True)
    environment_base_url = serializers.SerializerMethodField()
    pass_rate = serializers.FloatField(read_only=True)
    allure_report_url = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(source='execution.project_id', read_only=True)
    error_message = serializers.CharField(source='execution.error_message', read_only=True)
    
    class Meta:
        model = WebUITestSuiteExecutionDetail
        fields = [
            'id', 'execution', 'project_id', 'test_suite', 'test_suite_name',
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases',
            'pass_rate', 'browser', 'environment_name', 'environment_base_url',
            'start_time', 'end_time', 'duration', 'allure_report', 'allure_report_url',
            'error_message', 'log'
        ]
        read_only_fields = ['id', 'execution']
    
    def get_environment_base_url(self, obj):
        """获取环境base_url"""
        if obj.execution.environment:
            web_config = obj.execution.environment.get_web_config()
            return web_config.get('base_url', '') if web_config else ''
        return ''
    
    def get_allure_report_url(self, obj):
        """生成Allure报告访问URL - 优先返回持久化 media 路径"""
        if obj.allure_report:
            import os
            from django.conf import settings
            report_path = obj.allure_report
            # 持久化报告：media/allure_reports/<execution_id>/index.html
            norm_media = os.path.normpath(settings.MEDIA_ROOT)
            norm_path = os.path.normpath(os.path.abspath(report_path))
            if norm_media in norm_path and 'allure_reports' in norm_path and os.path.exists(report_path):
                return f"/media/allure_reports/{obj.execution_id}/index.html"
            if not os.path.exists(report_path):
                return None
            report_dir = os.path.dirname(report_path)
            report_filename = os.path.basename(report_path)
            try:
                relative_path = os.path.relpath(report_dir, settings.PLAYWRIGHT_REPORTS_ROOT)
                return f"/playwright-reports/{relative_path}/{report_filename}"
            except ValueError:
                return None
        return None


# ============ WebUI测试套件序列化器 ============

class WebUITestSuiteSerializer(serializers.ModelSerializer):
    """WebUI测试套件序列化器"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    test_cases_count = serializers.IntegerField(read_only=True)
    active_test_cases_count = serializers.IntegerField(read_only=True)
    test_cases = serializers.SerializerMethodField()
    
    class Meta:
        model = WebUITestSuite
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
        return [
            {
                'id': tc.id,
                'title': tc.title,
                'description': tc.description,
                'priority': tc.priority,
                'category': tc.category
            }
            for tc in test_cases
        ]


class WebUITestSuiteCreateSerializer(serializers.ModelSerializer):
    """WebUI测试套件创建序列化器"""
    
    class Meta:
        model = WebUITestSuite
        fields = [
            'name', 'description', 'project', 'status', 'tags'
        ]
        extra_kwargs = {
            'name': {'required': True},
            'description': {'required': False, 'allow_blank': True},
            'project': {'required': True},
            'status': {'required': False, 'default': 'active'},
            'tags': {'required': False, 'default': list}
        }
    
    def create(self, validated_data):
        # 自动设置用户
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class WebUITestSuiteUpdateSerializer(serializers.ModelSerializer):
    """WebUI测试套件更新序列化器"""
    
    class Meta:
        model = WebUITestSuite
        fields = [
            'name', 'description', 'status', 'tags'
        ]
        extra_kwargs = {
            'name': {'required': False},
            'description': {'required': False, 'allow_blank': True},
            'status': {'required': False},
            'tags': {'required': False}
        }


class WebUITestSuiteAddTestCaseSerializer(serializers.Serializer):
    """WebUI测试套件添加测试用例序列化器"""
    test_case_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="测试用例ID列表"
    )
    order = serializers.IntegerField(required=False, help_text="执行顺序")
    
    def validate_test_case_ids(self, value):
        """验证测试用例ID列表"""
        if not value:
            raise serializers.ValidationError("测试用例ID列表不能为空")
        
        # 项目归属由 URL project_id 对应的视图校验；这里仅校验 ID 存在，
        # 避免把同项目其他成员创建的用例误判为私有资源。
        existing_cases = WebUITestCase.objects.filter(
            id__in=value
        ).values_list('id', flat=True)
        
        missing_cases = set(value) - set(existing_cases)
        if missing_cases:
            raise serializers.ValidationError(f"测试用例不存在或无权限访问: {list(missing_cases)}")
        
        return value


class WebUITestCaseExecutionDetailSerializer(serializers.ModelSerializer):
    """单用例执行详情序列化器 - 用于单用例执行详情页面"""
    test_case_title = serializers.CharField(source='test_case.title', read_only=True)
    test_case_description = serializers.CharField(source='test_case.description', read_only=True)
    browser = serializers.CharField(source='execution.browser', read_only=True)
    environment_name = serializers.CharField(source='execution.environment.name', read_only=True)
    environment_base_url = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(source='execution.project_id', read_only=True)
    screenshot_path = serializers.SerializerMethodField()
    
    class Meta:
        model = WebUITestCaseExecutionDetail
        fields = [
            'id', 'execution', 'project_id', 'test_case', 'test_case_title', 'test_case_description',
            'status', 'browser', 'environment_name', 'environment_base_url',
            'start_time', 'end_time', 'duration',
            'error_message', 'log', 'screenshot_path', 'video_path'
        ]
        read_only_fields = ['id', 'execution']
    
    def get_environment_base_url(self, obj):
        """获取环境base_url"""
        if obj.execution.environment:
            web_config = obj.execution.environment.get_web_config()
            return web_config.get('base_url', '') if web_config else ''
        return ''

    def get_screenshot_path(self, obj):
        return safe_screenshot_relative_path(obj.screenshot_path)


class WebUITestSuiteCaseExecutionSerializer(serializers.ModelSerializer):
    """套件用例执行明细序列化器"""
    test_case_title = serializers.CharField(source='test_case.title', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = WebUITestSuiteCaseExecution
        fields = [
            'id', 'suite_execution', 'test_case', 'test_case_title',
            'name', 'status', 'status_display', 'duration',
            'error_message', 'log', 'screenshot_path', 'video_path', 'stdout'
        ]
        read_only_fields = ['id', 'suite_execution']

    screenshot_path = serializers.SerializerMethodField()

    def get_screenshot_path(self, obj):
        return safe_screenshot_relative_path(obj.screenshot_path)
