"""
Web Testing Serializers
用于Web UI自动化测试的序列化器
"""
from rest_framework import serializers
from .models import (
    WebUITestCase, WebUITestExecution, WebUITestSuite, WebUITestModule,
    WebUITestCaseExecutionDetail, WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution,
    WebPage, WebElement
)
from .script_contract import ScriptContractError, normalize_for_storage, store_script_content


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
            'status', 'trigger_type',
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
    
    class Meta:
        model = WebUITestSuiteExecutionDetail
        fields = [
            'id', 'execution', 'project_id', 'test_suite', 'test_suite_name',
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases',
            'pass_rate', 'browser', 'environment_name', 'environment_base_url',
            'start_time', 'end_time', 'duration', 'allure_report', 'allure_report_url',
            'log'
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


class WebUITestExecutionCreateSerializer(serializers.ModelSerializer):
    """WebUI测试执行创建序列化器"""
    project_id = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = WebUITestExecution
        fields = [
            'exec_type', 'name', 'description', 'trigger_type', 'environment', 'browser', 'project_id'
        ]
        read_only_fields = ['project_id']
        extra_kwargs = {
            'exec_type': {'required': True},
            'name': {'required': True},
            'description': {'required': False, 'allow_blank': True},
            'trigger_type': {'required': False, 'default': 'manual'},
            'environment': {'required': False},
            'browser': {'required': False, 'default': 'chromium'}
        }
    
    def create(self, validated_data):
        # 自动设置执行者
        validated_data['executor'] = self.context['request'].user
        return super().create(validated_data)


class WebUITestCaseExecutionDetailSerializer(serializers.ModelSerializer):
    """单用例执行详情序列化器 - 用于单用例执行详情页面"""
    test_case_title = serializers.CharField(source='test_case.title', read_only=True)
    test_case_description = serializers.CharField(source='test_case.description', read_only=True)
    browser = serializers.CharField(source='execution.browser', read_only=True)
    environment_name = serializers.CharField(source='execution.environment.name', read_only=True)
    environment_base_url = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(source='execution.project_id', read_only=True)
    
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
