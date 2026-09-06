"""
Scheduled Tasks Serializers
定时任务中心序列化器
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from projects.models import Environment
from .models import ScheduledTask, TaskExecutionLog
from .contracts import validate_suites, get_schedule_project
from .cron import next_run_time

User = get_user_model()


class ScheduledTaskSerializer(serializers.ModelSerializer):
    """定时任务序列化器"""
    
    # 关联字段
    user_name = serializers.CharField(source='user.username', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    environment_name = serializers.CharField(source='environment.name', read_only=True)
    suite_name = serializers.SerializerMethodField(read_only=True)
    
    # 执行统计
    total_executions = serializers.SerializerMethodField(read_only=True)
    success_rate = serializers.SerializerMethodField(read_only=True)
    last_execution_status = serializers.SerializerMethodField(read_only=True)
    last_report_status = serializers.SerializerMethodField(read_only=True)
    last_passed_cases = serializers.SerializerMethodField(read_only=True)
    last_failed_cases = serializers.SerializerMethodField(read_only=True)
    last_total_cases = serializers.SerializerMethodField(read_only=True)
    # 通知对象：嵌套返回 id/type/name/target_address，列表不暴露 webhook
    notice_targets = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = ScheduledTask
        fields = [
            'id', 'name', 'description', 'suite_type', 'suite_ids', 'suite_name',
            'cron_expression', 'environment', 'environment_name', 'status',
            'notice_targets', 'trigger_condition',
            'last_run_time', 'next_run_time', 'user', 'user_name', 'project', 'project_name',
            'created_at', 'updated_at',
            'total_executions', 'success_rate', 'last_execution_status', 'last_report_status',
            'last_passed_cases', 'last_failed_cases', 'last_total_cases',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_run_time', 'next_run_time']
    
    def get_suite_name(self, obj):
        """获取测试套件名称"""
        return obj.get_suite_name()
    
    def _get_last_log(self, obj):
        """获取最近一次执行日志（内部缓存）"""
        if not hasattr(obj, '_cached_last_log'):
            obj._cached_last_log = obj.execution_logs.order_by('-start_time').first()
        return obj._cached_last_log

    def get_total_executions(self, obj):
        """获取总执行次数"""
        return obj.execution_logs.count()

    def get_success_rate(self, obj):
        """成功率 = 最近一次执行的 passed_cases / total_cases"""
        last_log = self._get_last_log(obj)
        if last_log and last_log.total_cases > 0:
            return round((last_log.passed_cases / last_log.total_cases) * 100, 2)
        return 0

    def get_last_execution_status(self, obj):
        """获取最近一次执行状态"""
        last_log = self._get_last_log(obj)
        return last_log.status if last_log else None

    def get_last_report_status(self, obj):
        last_log = self._get_last_log(obj)
        if not last_log:
            return None
        if last_log.status in {'pending', 'running', 'cancelled'}:
            return last_log.status
        if last_log.linked_executions:
            from .reporting import summarize_linked_executions
            return summarize_linked_executions(last_log)['report_status']
        return 'error' if last_log.status == 'failed' else 'skipped'

    def get_last_passed_cases(self, obj):
        last_log = self._get_last_log(obj)
        return last_log.passed_cases if last_log else 0

    def get_last_failed_cases(self, obj):
        last_log = self._get_last_log(obj)
        return last_log.failed_cases if last_log else 0

    def get_last_total_cases(self, obj):
        last_log = self._get_last_log(obj)
        return last_log.total_cases if last_log else 0

    def get_notice_targets(self, obj):
        """返回通知对象详情列表：[{ id, type, name, target_address }, ...]，列表不暴露 webhook"""
        try:
            targets = obj.notice_targets.all()
        except Exception:
            return []
        result = []
        for t in targets:
            # type: 渠道类型；target_address 仅邮件类展示用，webhook 渠道不传
            target_address = getattr(t, 'email', None) or getattr(t, 'target_address', None) or ''
            result.append({
                'id': t.id,
                'type': getattr(t, 'channel_type', None) or (t.channel.channel_code if getattr(t, 'channel', None) else ''),
                'name': getattr(t, 'name', None) or '',
                'target_address': target_address,
            })
        return result
    
    def create(self, validated_data):
        """创建定时任务"""
        # 创建定时任务
        task = ScheduledTask.objects.create(**validated_data)
        
        return task
    
    def update(self, instance, validated_data):
        """更新定时任务"""
        # 更新定时任务
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        return instance


class ScheduledTaskCreateSerializer(serializers.ModelSerializer):
    """定时任务创建序列化器"""

    environment = serializers.PrimaryKeyRelatedField(
        queryset=Environment.objects.all(), required=False, allow_null=True,
    )
    suite_type = serializers.CharField(read_only=True)
    
    class Meta:
        model = ScheduledTask
        fields = [
            'name', 'description', 'suite_type', 'suite_ids', 'cron_expression',
            'environment', 'status', 'notice_targets', 'trigger_condition',
        ]
    
    def validate_suite_ids(self, value):
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError('至少选择一个测试套件')
        return value
    
    def validate_cron_expression(self, value):
        """验证cron表达式"""
        try:
            next_run_time(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return ' '.join(value.split())

    def validate(self, attrs):
        project = self.context.get('project') or getattr(self.instance, 'project', None)
        if project is None or project.project_type not in {'web', 'api'}:
            raise serializers.ValidationError('缺少有效的当前项目')
        suite_type = project.project_type
        if self.initial_data.get('suite_type', suite_type) != suite_type:
            raise serializers.ValidationError({'suite_type': '测试类型由当前项目确定，不能跨类型选择'})
        attrs['suite_type'] = suite_type
        request = self.context.get('request')
        if request and attrs.get('status', getattr(self.instance, 'status', 'active')) == 'active':
            get_schedule_project(project.pk, request.user, 'execute')
        validate_suites(project, attrs.get('suite_ids', getattr(self.instance, 'suite_ids', [])))
        for target in attrs.get('notice_targets', []):
            if target.project_id != project.pk:
                raise serializers.ValidationError({'notice_targets': '通知对象必须属于当前项目'})
        environment_provided = 'environment' in attrs
        environment = attrs.get('environment') if environment_provided else getattr(self.instance, 'environment', None)
        if suite_type == 'web':
            if environment_provided and environment is not None:
                raise serializers.ValidationError({'environment': 'WebUI 定时任务由脚本内完整网址执行，不接受环境。'})
            attrs['environment'] = None
            return attrs
        if environment is None:
            raise serializers.ValidationError({'environment': 'API 定时任务必须选择执行环境。'})
        if environment.project_id != project.pk or environment.category != 'api' or not environment.is_active:
            raise serializers.ValidationError({'environment': '请选择当前项目已启用的 API 环境'})
        return attrs
    
    def create(self, validated_data):
        """创建定时任务"""
        if not validated_data.get('suite_ids'):
            raise serializers.ValidationError("至少需要选择一个测试套件")
        notice_target_ids = validated_data.pop('notice_targets', None) or []
        task = ScheduledTask.objects.create(**validated_data)
        task.notice_targets.set(notice_target_ids)
        return task

    def update(self, instance, validated_data):
        """更新定时任务"""
        if 'suite_ids' in validated_data and not validated_data['suite_ids']:
            raise serializers.ValidationError("至少需要选择一个测试套件")
        notice_target_ids = validated_data.pop('notice_targets', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if notice_target_ids is not None:
            instance.notice_targets.set(notice_target_ids)
        return instance


class ExecutionLogSummaryMixin:
    def _summary(self, obj):
        cache = self.__dict__.setdefault('_report_summary_cache', {})
        key = obj.pk if obj.pk is not None else id(obj)
        if key not in cache:
            from .reporting import ExecutionSummary, summarize_linked_executions
            if obj.linked_executions:
                cache[key] = summarize_linked_executions(obj)
            else:
                # Preflight failures have no child executions.  No cases must
                # never be presented as a successful test run.
                cache[key] = ExecutionSummary(
                    total_cases=obj.total_cases,
                    passed_cases=obj.passed_cases,
                    failed_cases=obj.failed_cases,
                    skipped_cases=obj.skipped_cases,
                    incomplete_cases=max(obj.total_cases - obj.passed_cases - obj.failed_cases - obj.skipped_cases, 0),
                    execution_errors=int(obj.status == 'failed'),
                    is_running=obj.status in {'pending', 'running'},
                ).as_dict()
        return cache[key]


class TaskExecutionLogSerializer(ExecutionLogSummaryMixin, serializers.ModelSerializer):
    """任务执行日志序列化器"""
    
    task_name = serializers.CharField(source='task.name', read_only=True)
    suite_type = serializers.CharField(source='task.suite_type', read_only=True)
    duration = serializers.SerializerMethodField(read_only=True)
    total_cases = serializers.SerializerMethodField(read_only=True)
    passed_cases = serializers.SerializerMethodField(read_only=True)
    failed_cases = serializers.SerializerMethodField(read_only=True)
    skipped_cases = serializers.SerializerMethodField(read_only=True)
    success_rate = serializers.SerializerMethodField(read_only=True)
    incomplete_cases = serializers.SerializerMethodField(read_only=True)
    error_cases = serializers.SerializerMethodField(read_only=True)
    execution_errors = serializers.SerializerMethodField(read_only=True)
    report_status = serializers.SerializerMethodField(read_only=True)
    task_status = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = TaskExecutionLog
        fields = [
            'id', 'task', 'task_name', 'suite_type', 'start_time', 'end_time',
            'duration', 'status', 'result_log', 'step_log', 'error_message',
            'total_cases', 'passed_cases', 'failed_cases', 'skipped_cases',
            'incomplete_cases', 'error_cases', 'execution_errors', 'report_status', 'task_status',
            'success_rate', 'report_url', 'linked_executions', 'notification_sent_at', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_duration(self, obj):
        """获取执行时长"""
        if obj.duration:
            return str(obj.duration)
        return None
    
    def get_success_rate(self, obj):
        """获取成功率"""
        return self._summary(obj)['success_rate']

    def get_total_cases(self, obj):
        return self._summary(obj)['total_cases']

    def get_passed_cases(self, obj):
        return self._summary(obj)['passed_cases']

    def get_failed_cases(self, obj):
        return self._summary(obj)['failed_cases']

    def get_skipped_cases(self, obj):
        return self._summary(obj)['skipped_cases']

    def get_incomplete_cases(self, obj):
        return self._summary(obj)['incomplete_cases']

    def get_error_cases(self, obj):
        return self._summary(obj)['error_cases']

    def get_execution_errors(self, obj):
        return self._summary(obj)['execution_errors']

    def get_report_status(self, obj):
        return self._summary(obj)['report_status']

    def get_task_status(self, obj):
        return 'running' if self._summary(obj).get('is_running') else 'completed'


class TaskExecutionLogListSerializer(ExecutionLogSummaryMixin, serializers.ModelSerializer):
    """任务执行日志列表序列化器（简化版）"""
    
    task_name = serializers.CharField(source='task.name', read_only=True)
    suite_type = serializers.CharField(source='task.suite_type', read_only=True)
    duration = serializers.SerializerMethodField(read_only=True)
    total_cases = serializers.SerializerMethodField(read_only=True)
    passed_cases = serializers.SerializerMethodField(read_only=True)
    failed_cases = serializers.SerializerMethodField(read_only=True)
    skipped_cases = serializers.SerializerMethodField(read_only=True)
    incomplete_cases = serializers.SerializerMethodField(read_only=True)
    success_rate = serializers.SerializerMethodField(read_only=True)
    task_status = serializers.SerializerMethodField(read_only=True)
    report_status = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = TaskExecutionLog
        fields = [
            'id', 'task_name', 'suite_type', 'start_time', 'end_time', 'duration',
            'status', 'total_cases', 'passed_cases', 'failed_cases',
            'skipped_cases', 'incomplete_cases', 'task_status', 'report_status', 'success_rate', 'report_url', 'linked_executions'
        ]
    
    def get_duration(self, obj):
        """获取执行时长"""
        if obj.duration:
            return str(obj.duration)
        return None

    def get_incomplete_cases(self, obj):
        return self._summary(obj)['incomplete_cases']

    def get_success_rate(self, obj):
        return self._summary(obj)['success_rate']

    def get_task_status(self, obj):
        return 'running' if self._summary(obj)['is_running'] else 'completed'

    def get_report_status(self, obj):
        return self._summary(obj)['report_status']

    def get_total_cases(self, obj):
        return self._summary(obj)['total_cases']

    def get_passed_cases(self, obj):
        return self._summary(obj)['passed_cases']

    def get_failed_cases(self, obj):
        return self._summary(obj)['failed_cases']

    def get_skipped_cases(self, obj):
        return self._summary(obj)['skipped_cases']


class TaskRunSerializer(serializers.Serializer):
    """手动执行任务序列化器"""
    
    def validate(self, attrs):
        """验证任务是否可以执行"""
        task = self.context['task']
        
        if task.status not in {'active', 'paused'}:
            raise serializers.ValidationError('当前任务状态不支持手动执行')
        validate_suites(task.project, task.suite_ids)
        
        return attrs


class TaskStatusUpdateSerializer(serializers.Serializer):
    """任务状态更新序列化器"""
    
    status = serializers.ChoiceField(choices=ScheduledTask.STATUS_CHOICES)
    
    def validate_status(self, value):
        """验证状态值"""
        if value not in [choice[0] for choice in ScheduledTask.STATUS_CHOICES]:
            raise serializers.ValidationError("无效的状态值")
        return value


class SuiteChoiceSerializer(serializers.Serializer):
    """测试套件选择序列化器"""
    
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(required=False)
    total_cases = serializers.IntegerField(required=False)
    status = serializers.CharField()
    selectable = serializers.BooleanField()
    unavailable_reason = serializers.CharField(allow_blank=True)
