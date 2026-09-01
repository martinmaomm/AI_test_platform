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
    WebUIScriptGeneration,
)
from .generation_repository import create_generation
from .generation_preflight import exploration_requires_write_confirmation
from .generation_save_state import is_generation_saved
from .generation_security import (
    GenerationInputSecurityError,
    build_safe_target_url,
    clear_temporary_credentials,
    extract_inline_login_credentials,
    find_suspected_credentials,
    normalize_start_path,
    redact_text,
    store_temporary_credentials,
    validate_temporary_credentials,
)
from .script_contract import ScriptContractError, normalize_for_storage, store_script_content
from .execution_diagnostics import safe_screenshot_relative_path
from .execution_variables import ExecutionVariableError, normalize_variable_definitions
from .generation_workspace import workspace_for_response


class WebUIScriptGenerationSerializer(serializers.ModelSerializer):
    """Safe, persistent view of one WebUI script-generation task."""

    environment_id = serializers.IntegerField(read_only=True)
    environment_name = serializers.CharField(source='environment.name', read_only=True)
    test_case_id = serializers.IntegerField(read_only=True, allow_null=True)
    module_id = serializers.IntegerField(read_only=True, allow_null=True)
    module_name = serializers.CharField(source='module.name', read_only=True, allow_null=True)
    is_saved = serializers.SerializerMethodField()
    workspace = serializers.SerializerMethodField()

    def get_is_saved(self, obj):
        return is_generation_saved(obj)

    def get_workspace(self, obj):
        return workspace_for_response(obj)

    class Meta:
        model = WebUIScriptGeneration
        fields = [
            'id', 'project', 'user', 'environment_id', 'environment_name', 'test_case_id',
            'module_id', 'module_name', 'is_saved',
            'celery_task_id', 'status', 'current_stage', 'progress',
            'start_path', 'target_url_safe', 'description_safe', 'scenario_spec',
            'exploration_snapshot', 'script_draft', 'quality_report', 'warnings',
            'workspace',
            'model_info', 'tool_stats', 'repair_count', 'credentials_required',
            'revision', 'resume_count', 'clarifications',
            'credentials_provided', 'credentials_expired', 'error_code', 'error_message',
            'cancel_requested_at', 'started_at', 'completed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'project', 'user', 'environment_id', 'environment_name', 'test_case_id',
            'module_id', 'module_name', 'is_saved',
            'celery_task_id', 'status', 'current_stage', 'progress',
            'start_path', 'target_url_safe', 'description_safe', 'scenario_spec',
            'exploration_snapshot', 'script_draft', 'quality_report', 'warnings',
            'workspace',
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
    module_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
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
        unsupported_findings = set(findings) - {'login_pair'}
        if unsupported_findings:
            raise serializers.ValidationError({
                'description': '场景描述中包含无法安全识别的密码、令牌或密钥，请仅使用“登录账号 用户名 密码”格式指定被测环境登录信息。'
            })
        try:
            inline_credentials = extract_inline_login_credentials(description)
        except GenerationInputSecurityError as exc:
            raise serializers.ValidationError({'description': str(exc)}) from exc
        explicit_credentials = attrs.get('temporary_credentials')
        if inline_credentials and explicit_credentials and inline_credentials != explicit_credentials:
            raise serializers.ValidationError({
                'temporary_credentials': '测试描述与登录信息输入框中的账号密码不一致，请只保留一组。'
            })
        if inline_credentials:
            attrs['temporary_credentials'] = inline_credentials

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

        module = None
        if attrs.get('module_id') is not None:
            module = WebUITestModule.objects.filter(pk=attrs['module_id'], project=project).first()
            if module is None:
                raise serializers.ValidationError({'module_id': '业务模块不存在或不属于当前项目'})
        if module is None:
            module = WebUITestModule.ensure_default(project.id)

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
        attrs['module'] = module
        attrs['normalized_start_path'] = start_path
        attrs['base_url'] = base_url
        attrs['model_config'] = model_config
        return attrs

    def create(self, validated_data):
        project = self.context['project']
        user = self.context['request'].user
        credentials = validated_data.pop('temporary_credentials', None)
        environment = validated_data.pop('environment')
        module = validated_data.pop('module')
        start_path = validated_data.pop('normalized_start_path')
        base_url = validated_data.pop('base_url')
        model_config = validated_data.pop('model_config')
        validated_data.pop('environment_id', None)
        validated_data.pop('module_id', None)
        validated_data.pop('url', None)
        validated_data.pop('model_config_id', None)
        description = validated_data.pop('description')

        generation = None
        try:
            generation = create_generation(
                project=project,
                user=user,
                environment=environment,
                module=module,
                start_path=start_path,
                target_url_safe=build_safe_target_url(base_url, start_path),
                description_safe=redact_text(description),
                credentials_provided=credentials is not None,
                model_info={
                    'config_id': model_config.id,
                    'provider': model_config.provider,
                    'provider_name': model_config.provider_name,
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
    mode = serializers.ChoiceField(choices=['draft', 'verified'], required=False)
    expected_revision = serializers.IntegerField(min_value=0, required=False)

    def validate_title(self, value):
        if find_suspected_credentials(value):
            raise serializers.ValidationError('标题不能包含账号、密码或密钥。')
        return redact_text(value)

    def validate(self, attrs):
        if attrs.get('mode') and 'expected_revision' not in attrs:
            raise serializers.ValidationError({'expected_revision': '保存工作区脚本必须提供当前 revision。'})
        return attrs


class WebUIScriptGenerationDraftSerializer(serializers.Serializer):
    script_draft = serializers.CharField(allow_blank=False, trim_whitespace=False, max_length=200000)
    variables = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    expected_revision = serializers.IntegerField(min_value=0)

    def validate_script_draft(self, value):
        if not value.strip():
            raise serializers.ValidationError('草稿不能为空；可以保存尚未修正的代码。')
        return value

    def validate_variables(self, value):
        try:
            return normalize_variable_definitions(value)
        except ExecutionVariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class WebUIScriptGenerationDebugSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=0)
    confirm_execution = serializers.BooleanField()
    runtime_variables = serializers.ListField(child=serializers.DictField(), required=False, default=list)

    def validate_confirm_execution(self, value):
        if value is not True:
            raise serializers.ValidationError('调试会实际执行脚本，必须明确确认 confirm_execution=true。')
        return value

    def validate_runtime_variables(self, value):
        try:
            return normalize_variable_definitions(value)
        except ExecutionVariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class WebUIScriptGenerationRepairSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=0)


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

        if description:
            unsupported_findings = set(find_suspected_credentials(description)) - {'login_pair'}
            if unsupported_findings:
                raise serializers.ValidationError({
                    'description': '修订描述中包含无法安全识别的密码、令牌或密钥，请仅使用“登录账号 用户名 密码”格式。'
                })
            try:
                inline_credentials = extract_inline_login_credentials(description)
            except GenerationInputSecurityError as exc:
                raise serializers.ValidationError({'description': str(exc)}) from exc
            if inline_credentials and credentials and inline_credentials != credentials:
                raise serializers.ValidationError({
                    'temporary_credentials': '修订描述与登录信息输入框中的账号密码不一致，请只保留一组。'
                })
            if inline_credentials:
                attrs['temporary_credentials'] = inline_credentials
                credentials = inline_credentials
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
            if generation.error_code in {'EXPLORATION_WRITE_CONFIRMATION_REQUIRED', 'EXPLORATION_EXTRA_RISK_BLOCKED'}:
                if generation.error_code == 'EXPLORATION_EXTRA_RISK_BLOCKED' and not description:
                    raise serializers.ValidationError({'description': '请修订测试目标，移除当前不支持的额外风险操作。'})
                if exploration_requires_write_confirmation(description or generation.description_safe):
                    raise serializers.ValidationError({
                        'description': '本次自动探索支持目标内测试数据增删改查；请移除审批、支付、发布及未授权文件/外部消息操作后继续。'
                    })
            elif generation.error_code == 'INPUT_AMBIGUOUS':
                auto_explore = (
                    generation.current_stage == WebUIScriptGeneration.Stage.PREFLIGHTING
                    and not answers
                )
                if not auto_explore:
                    expected_questions = self._questions(generation)
                    submitted = {item['question']: item['answer'] for item in answers}
                    if len(submitted) != len(answers):
                        raise serializers.ValidationError({'clarification_answers': '待确认项不能重复。'})
                    if set(submitted) != set(expected_questions):
                        raise serializers.ValidationError({'clarification_answers': '请逐项回答页面探索后仍未解决的全部问题。'})
            else:
                if not description:
                    raise serializers.ValidationError({'description': '请修订测试描述后继续。'})

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
        fields = [
            'id', 'name', 'parent', 'order', 'is_default',
            'project', 'children', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_default', 'created_at', 'updated_at']
        extra_kwargs = {'project': {'required': False}}

    def get_children(self, obj):
        """递归获取子模块"""
        children = obj.children.all().order_by('order', 'id')
        return WebUITestModuleSerializer(children, many=True).data


class WebUITestCaseSerializer(serializers.ModelSerializer):
    """Compact list representation for one executable script."""
    created_by_username = serializers.CharField(source='user.username', read_only=True)
    module_name = serializers.CharField(source='module.name', read_only=True, allow_null=True)
    module_id = serializers.PrimaryKeyRelatedField(
        queryset=WebUITestModule.objects.all(), required=False, allow_null=True, source='module'
    )
    has_script = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = WebUITestCase
        fields = [
            'id', 'title', 'description', 'created_by_username',
            'module_id', 'module_name', 'has_script', 'variables',
            'script_source', 'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
            'created_at', 'updated_at',
            'last_execute_status', 'last_execute_time', 'last_error_message',
        ]
        read_only_fields = [
            'id', 'created_by_username', 'created_at', 'updated_at',
            'last_execute_status', 'last_execute_time', 'last_error_message',
            'script_source', 'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
        ]


class WebUITestCaseDetailSerializer(serializers.ModelSerializer):
    """Editable script, description, classification and variables."""
    created_by_username = serializers.CharField(source='user.username', read_only=True)
    module_name = serializers.CharField(source='module.name', read_only=True, allow_null=True)
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

    def validate_variables(self, value):
        try:
            return normalize_variable_definitions(value)
        except ExecutionVariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc
    
    class Meta:
        model = WebUITestCase
        fields = [
            'id', 'title', 'description', 'created_by_username',
            'module_id', 'module_name', 'has_script', 'variables',
            'test_script_content', 'script_source', 'script_status', 'script_framework',
            'script_version', 'script_validation_error', 'generation_metadata',
            'created_at', 'updated_at',
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
        variables_changed = 'variables' in validated_data and validated_data['variables'] != instance.variables
        if 'module' in validated_data and validated_data['module'] is None:
            validated_data['module'] = WebUITestModule.ensure_default(instance.project_id)
        module = validated_data.get('module')
        if module is not None and module.project_id != instance.project_id:
            raise serializers.ValidationError({'module_id': '业务模块必须属于当前项目'})
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
        if variables_changed:
            instance.last_execute_status = 'untested'
            instance.last_execute_time = None
            instance.last_error_message = ''
            metadata = dict(instance.generation_metadata or {})
            metadata.pop('verification', None)
            instance.generation_metadata = metadata
            instance.save(update_fields=['last_execute_status', 'last_execute_time', 'last_error_message', 'generation_metadata', 'updated_at'])
        return instance


class WebUITestCaseCreateSerializer(serializers.ModelSerializer):
    """Create one manually authored independent script."""
    
    class Meta:
        model = WebUITestCase
        fields = [
            'title', 'description', 'project', 'module', 'variables',
            'test_script_content', 'script_source', 'script_status', 'script_framework',
            'script_version', 'script_validation_error', 'generation_metadata'
        ]
        extra_kwargs = {
            'title': {'required': True},
            'description': {'required': True},
            'project': {'required': True},
            'module': {'required': False, 'allow_null': True},
            'variables': {'required': False, 'default': list},
            'test_script_content': {'required': True, 'allow_blank': False},
        }
        read_only_fields = [
            'script_status', 'script_framework', 'script_version',
            'script_validation_error', 'generation_metadata',
        ]
    
    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        script_content = validated_data.pop('test_script_content', None)
        script_source = validated_data.pop('script_source', 'manual')
        if validated_data.get('module') is None:
            validated_data['module'] = WebUITestModule.ensure_default(validated_data['project'].id)
        test_case = super().create(validated_data)
        try:
            return store_script_content(test_case, script_content, source=script_source)
        except ScriptContractError as exc:
            test_case.delete()
            raise serializers.ValidationError({'test_script_content': str(exc)})

    def validate(self, attrs):
        project = attrs.get('project')
        module = attrs.get('module')
        if project is not None and module is not None and module.project_id != project.id:
            raise serializers.ValidationError({'module': '业务模块必须属于当前项目'})
        return attrs

    def validate_test_script_content(self, value):
        if value in (None, ''):
            return value
        try:
            return normalize_for_storage(value)
        except ScriptContractError as exc:
            raise serializers.ValidationError(str(exc))

    def validate_variables(self, value):
        try:
            return normalize_variable_definitions(value)
        except ExecutionVariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc


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
    test_suite_name = serializers.CharField(source='execution.name', read_only=True)
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
            'status', 'status_display', 'tags', 'variables',
            # 统计信息
            'test_cases_count', 'active_test_cases_count', 'test_cases',
            # 时间信息
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'user', 'created_at', 'updated_at'
        ]
    
    def get_test_cases(self, obj):
        memberships = obj.case_memberships.select_related('test_case').order_by('order', 'id')
        return [
            {
                'id': item.test_case.id,
                'title': item.test_case.title,
                'description': item.test_case.description,
                'order': item.order,
                'script_status': item.test_case.script_status,
            }
            for item in memberships
        ]


class WebUITestSuiteCreateSerializer(serializers.ModelSerializer):
    """WebUI测试套件创建序列化器"""
    
    class Meta:
        model = WebUITestSuite
        fields = [
            'name', 'description', 'project', 'status', 'tags', 'variables'
        ]
        extra_kwargs = {
            'name': {'required': True},
            'description': {'required': False, 'allow_blank': True},
            'project': {'required': True},
            'status': {'required': False, 'default': 'active'},
            'tags': {'required': False, 'default': list},
            'variables': {'required': False, 'default': list},
        }

    def validate_variables(self, value):
        try:
            return normalize_variable_definitions(value)
        except ExecutionVariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc
    
    def create(self, validated_data):
        # 自动设置用户
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class WebUITestSuiteUpdateSerializer(serializers.ModelSerializer):
    """WebUI测试套件更新序列化器"""
    
    class Meta:
        model = WebUITestSuite
        fields = [
            'name', 'description', 'status', 'tags', 'variables'
        ]
        extra_kwargs = {
            'name': {'required': False},
            'description': {'required': False, 'allow_blank': True},
            'status': {'required': False},
            'tags': {'required': False},
            'variables': {'required': False},
        }

    def validate_variables(self, value):
        try:
            return normalize_variable_definitions(value)
        except ExecutionVariableError as exc:
            raise serializers.ValidationError(str(exc)) from exc


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
    test_case_title = serializers.CharField(source='execution.name', read_only=True)
    test_case_description = serializers.CharField(source='execution.description', read_only=True)
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
    test_case_title = serializers.CharField(source='name', read_only=True)
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
