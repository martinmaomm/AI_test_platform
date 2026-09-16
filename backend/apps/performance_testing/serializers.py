import json
import math
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from .constants import (
    ALLOWED_HTTP_METHODS, MAX_DURATION_SECONDS, MAX_REQUEST_BYTES,
    MAX_SPAWN_RATE, MAX_STEPS, MAX_USERS,
)
from .models import PerformanceNode, PerformancePlan, PerformanceRun, PerformanceTarget


_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')


class StrictSerializer(serializers.Serializer):
    """Reject input keys that are not part of the public contract."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError('请求包含不支持的字段。')
        return super().to_internal_value(data)


class StrictModelSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError('请求包含不支持的字段。')
        return super().to_internal_value(data)


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail('invalid')
        return super().to_internal_value(data)


class StrictFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            self.fail('invalid')
        return super().to_internal_value(data)


class StrictPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail('incorrect_type', data_type=type(data).__name__)
        return super().to_internal_value(data)


def _validate_finite_number(value, *, minimum, maximum, field_name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise serializers.ValidationError(f'{field_name} 必须是有限数值。')
    if value < minimum or value > maximum:
        raise serializers.ValidationError(f'{field_name} 必须在 {minimum} 到 {maximum} 之间。')
    return value


def _validate_json_value(value, *, depth=0):
    if depth > 10:
        raise serializers.ValidationError('JSON 嵌套层级过深。')
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise serializers.ValidationError('JSON 不允许 NaN 或无穷值。')
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise serializers.ValidationError('JSON 对象键必须是字符串。')
            _validate_json_value(item, depth=depth + 1)
        return
    raise serializers.ValidationError('值不是受支持的 JSON 类型。')


class PerformanceTargetSerializer(StrictModelSerializer):
    allowed_methods = serializers.ListField(
        child=serializers.ChoiceField(choices=ALLOWED_HTTP_METHODS),
        allow_empty=False,
        default=lambda: ['GET'],
    )

    class Meta:
        model = PerformanceTarget
        fields = ('id', 'name', 'base_url', 'allowed_methods', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_name(self, value):
        value = value.strip()
        if not value or _CONTROL_RE.search(value):
            raise serializers.ValidationError('名称不能为空或包含控制字符。')
        return value

    def validate_base_url(self, value):
        if (
            value != value.strip() or any(char.isspace() for char in value)
            or _CONTROL_RE.search(value) or '\\' in value or '?' in value or '#' in value
        ):
            raise serializers.ValidationError('目标地址格式无效。')
        try:
            parsed = urlsplit(value)
            _ = parsed.port
            URLValidator(schemes=('http', 'https'))(value)
        except (ValueError, DjangoValidationError):
            raise serializers.ValidationError('目标地址格式无效。')
        if (
            parsed.scheme.lower() not in {'http', 'https'}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {'', '/'}
            or parsed.netloc.endswith(':')
        ):
            raise serializers.ValidationError('目标地址必须是不含用户信息、业务路径、查询或片段的 HTTP(S) origin。')
        return value

    def validate_allowed_methods(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError('请求方法不能重复。')
        return value

    def validate(self, attrs):
        methods = attrs.get('allowed_methods')
        if methods is not None and self.instance is not None:
            disallowed = set()
            for plan in self.instance.plans.only('steps'):
                disallowed.update(
                    step.get('method') for step in (plan.steps or [])
                    if isinstance(step, dict) and step.get('method') not in methods
                )
            if disallowed:
                raise serializers.ValidationError({
                    'allowed_methods': f'已有计划仍使用方法：{", ".join(sorted(disallowed))}。',
                })
        return attrs


class PlanStepSerializer(StrictSerializer):
    name = serializers.CharField(max_length=200)
    method = serializers.ChoiceField(choices=ALLOWED_HTTP_METHODS)
    path = serializers.CharField(max_length=2048)
    expected_status = StrictIntegerField(min_value=100, max_value=599, default=200)
    headers = serializers.DictField(default=dict)
    body = serializers.JSONField(allow_null=True, default=None)

    def validate_name(self, value):
        value = value.strip()
        if not value or _CONTROL_RE.search(value):
            raise serializers.ValidationError('步骤名称不能为空或包含控制字符。')
        return value

    def validate_path(self, value):
        if (
            not value.startswith('/') or value.startswith('//') or '\\' in value
            or _CONTROL_RE.search(value)
        ):
            raise serializers.ValidationError('path 必须以单个 / 开头，且不能包含绝对 URL、反斜线或控制字符。')
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            raise serializers.ValidationError('path 不能是绝对 URL。')
        return value

    def validate_headers(self, value):
        if len(value) > 50:
            raise serializers.ValidationError('headers 最多包含 50 项。')
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise serializers.ValidationError('header 名称和值必须是字符串。')
            if not key or len(key) > 128 or len(item) > 4096 or _CONTROL_RE.search(key) or _CONTROL_RE.search(item):
                raise serializers.ValidationError('header 名称或值无效。')
            normalized[key] = item
        return normalized

    def validate_body(self, value):
        _validate_json_value(value)
        return value


class PerformancePlanSerializer(StrictModelSerializer):
    target_id = StrictPrimaryKeyRelatedField(
        source='target', queryset=PerformanceTarget.objects.none(),
    )
    users = StrictIntegerField(min_value=1, max_value=MAX_USERS, default=1)
    spawn_rate = StrictFloatField(min_value=0.000001, max_value=MAX_SPAWN_RATE, default=1)
    duration_seconds = StrictIntegerField(min_value=1, max_value=MAX_DURATION_SECONDS, default=30)
    wait_seconds = StrictFloatField(min_value=0.1, max_value=60, default=1)
    steps = PlanStepSerializer(many=True, allow_empty=False)

    class Meta:
        model = PerformancePlan
        fields = (
            'id', 'name', 'description', 'target_id', 'users', 'spawn_rate',
            'duration_seconds', 'wait_seconds', 'steps', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project = self.context.get('project')
        if project is not None:
            self.fields['target_id'].queryset = PerformanceTarget.objects.filter(project=project)

    def validate_name(self, value):
        value = value.strip()
        if not value or _CONTROL_RE.search(value):
            raise serializers.ValidationError('名称不能为空或包含控制字符。')
        return value

    def validate_spawn_rate(self, value):
        return _validate_finite_number(
            value, minimum=0.000001, maximum=MAX_SPAWN_RATE, field_name='spawn_rate',
        )

    def validate_wait_seconds(self, value):
        return _validate_finite_number(value, minimum=0.1, maximum=60, field_name='wait_seconds')

    def validate_steps(self, value):
        if len(value) > MAX_STEPS:
            raise serializers.ValidationError(f'步骤最多 {MAX_STEPS} 项。')
        return value

    def validate(self, attrs):
        target = attrs.get('target') or (self.instance.target if self.instance else None)
        steps = attrs.get('steps') or (self.instance.steps if self.instance else [])
        if target is not None:
            disallowed = sorted({step['method'] for step in steps} - set(target.allowed_methods or []))
            if disallowed:
                raise serializers.ValidationError({
                    'steps': f'步骤方法未获目标批准：{", ".join(disallowed)}。',
                })
        candidate = {
            'name': attrs.get('name', getattr(self.instance, 'name', '')),
            'description': attrs.get('description', getattr(self.instance, 'description', '')),
            'target_id': getattr(target, 'pk', None),
            'users': attrs.get('users', getattr(self.instance, 'users', 1)),
            'spawn_rate': attrs.get('spawn_rate', getattr(self.instance, 'spawn_rate', 1)),
            'duration_seconds': attrs.get('duration_seconds', getattr(self.instance, 'duration_seconds', 30)),
            'wait_seconds': attrs.get('wait_seconds', getattr(self.instance, 'wait_seconds', 1)),
            'steps': steps,
        }
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_REQUEST_BYTES:
            raise serializers.ValidationError('计划 JSON 内容过大。')
        return attrs


class PerformanceNodeSerializer(StrictModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceNode
        fields = (
            'id', 'name', 'network_mode', 'labels', 'status', 'last_seen_at',
            'agent_version', 'engine_version', 'protocol_version', 'resources', 'created_at',
        )
        read_only_fields = (
            'id', 'status', 'last_seen_at', 'agent_version', 'engine_version',
            'protocol_version', 'resources', 'created_at',
        )

    def get_status(self, obj):
        return obj.status_at(self.context.get('current_time'))

    def validate_name(self, value):
        value = value.strip()
        if not value or _CONTROL_RE.search(value):
            raise serializers.ValidationError('名称不能为空或包含控制字符。')
        return value

    def validate_labels(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('labels 必须是对象。')
        if len(value) > 20:
            raise serializers.ValidationError('labels 最多包含 20 项。')
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise serializers.ValidationError('label 名称和值必须是字符串。')
            if not key or len(key) > 64 or len(item) > 200 or _CONTROL_RE.search(key) or _CONTROL_RE.search(item):
                raise serializers.ValidationError('label 名称或值无效。')
            normalized[key] = item
        return normalized


class AgentVersionSerializer(StrictSerializer):
    protocol_version = StrictIntegerField()
    agent_version = serializers.CharField(max_length=32)
    engine_version = serializers.CharField(max_length=32)


class EnrollmentSerializer(AgentVersionSerializer):
    enrollment_token = serializers.CharField(max_length=256, write_only=True)


class ResourceSerializer(StrictSerializer):
    cpu_percent = StrictFloatField()
    memory_percent = StrictFloatField()

    def validate_cpu_percent(self, value):
        return _validate_finite_number(value, minimum=0, maximum=100, field_name='cpu_percent')

    def validate_memory_percent(self, value):
        return _validate_finite_number(value, minimum=0, maximum=100, field_name='memory_percent')


class HeartbeatSerializer(AgentVersionSerializer):
    resources = ResourceSerializer()
    run_report = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate_run_report(self, value):
        if value is None:
            return None
        serializer = RunReportSerializer(data=value)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class RunReportSerializer(StrictSerializer):
    run_id = serializers.UUIDField()
    sequence = StrictIntegerField(min_value=1, max_value=9223372036854775807)
    state = serializers.ChoiceField(choices=('preparing', 'ready', 'running', 'stopped', 'failed'))
    reason_code = serializers.CharField(max_length=64, allow_blank=True)
    reason = serializers.CharField(max_length=2000, allow_blank=True)


class PerformanceRunCreateSerializer(StrictSerializer):
    node_id = serializers.UUIDField()
    request_id = serializers.UUIDField()


class PerformanceRunListSerializer(serializers.ModelSerializer):
    plan_name = serializers.SerializerMethodField()
    node_name = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceRun
        fields = (
            'id', 'plan_id', 'plan_name', 'node_id', 'node_name', 'status',
            'created_at', 'started_at', 'finished_at', 'reason_code', 'reason',
            'latest_metrics',
        )
        read_only_fields = fields

    def get_plan_name(self, obj):
        return (obj.snapshot or {}).get('plan_name', '')

    def get_node_name(self, obj):
        return obj.node.name


class PerformanceRunDetailSerializer(PerformanceRunListSerializer):
    class Meta(PerformanceRunListSerializer.Meta):
        fields = PerformanceRunListSerializer.Meta.fields + ('metrics_samples', 'snapshot')
