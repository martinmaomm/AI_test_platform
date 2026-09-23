import json
import math
import re
from urllib.parse import unquote, urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from .constants import (
    ALLOWED_HTTP_METHODS, DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS,
    MAX_CONNECT_TIMEOUT_SECONDS, MAX_DURATION_SECONDS, MAX_REQUEST_BYTES,
    MAX_NODES_PER_RUN, MAX_READ_TIMEOUT_SECONDS, MAX_SPAWN_RATE, MAX_STEPS, MAX_USERS,
)
from .models import (
    PerformanceNode, PerformancePlan, PerformanceRun, PerformanceRunNode,
    PerformanceTarget,
)


_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_VARIABLE_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
_VARIABLE_REF_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]{0,63})\}')
_SELECTOR_RE = re.compile(
    r'^(?:status_code|text|headers\.[!#$%&\'*+.^_`|~0-9A-Za-z-]{1,128}'
    r'|body(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:0|[1-9][0-9]*)\])*)$'
)
_FORBIDDEN_HEADERS = {
    'host', 'content-length', 'transfer-encoding', 'connection',
    'proxy-authorization', 'proxy-connection',
}


class StrictSerializer(serializers.Serializer):
    """Reject input keys that are not part of the public contract."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({
                    'non_field_errors': ['请求包含不支持的字段。'],
                })
        return super().to_internal_value(data)


class StrictModelSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({
                    'non_field_errors': ['请求包含不支持的字段。'],
                })
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


class PlanExtractSerializer(StrictSerializer):
    name = serializers.CharField(max_length=64)
    check = serializers.CharField(max_length=512)

    def validate_name(self, value):
        if not _VARIABLE_NAME_RE.fullmatch(value):
            raise serializers.ValidationError('变量名只能使用字母、数字和下划线，且不能以数字开头。')
        return value

    def validate_check(self, value):
        if not _SELECTOR_RE.fullmatch(value):
            raise serializers.ValidationError('提取选择器不受支持。')
        return value


class PlanAssertionSerializer(StrictSerializer):
    check = serializers.CharField(max_length=512)
    comparator = serializers.ChoiceField(choices=(
        'eq', 'ne', 'contains', 'not_contains', 'gt', 'ge', 'lt', 'le',
        'type', 'length', 'length_gt', 'exists',
    ))
    expected = serializers.JSONField(allow_null=True)

    def validate_check(self, value):
        if not _SELECTOR_RE.fullmatch(value):
            raise serializers.ValidationError('断言选择器不受支持。')
        return value

    def validate(self, attrs):
        expected = attrs.get('expected')
        comparator = attrs.get('comparator')
        _validate_json_value(expected)
        if comparator == 'type' and (
            not isinstance(expected, str) or expected not in {
                'null', 'boolean', 'number', 'string', 'object', 'list',
            }
        ):
            raise serializers.ValidationError({'expected': 'type 断言的 expected 类型名称无效。'})
        if comparator in {'length', 'length_gt'} and (
            isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
        ):
            raise serializers.ValidationError({'expected': '长度断言的 expected 必须是非负整数。'})
        if comparator in {'gt', 'ge', 'lt', 'le'} and (
            isinstance(expected, bool) or not isinstance(expected, (int, float))
            or not math.isfinite(expected)
        ):
            raise serializers.ValidationError({'expected': '数值比较的 expected 必须是有限数值。'})
        if comparator == 'exists' and not isinstance(expected, bool):
            raise serializers.ValidationError({'expected': 'exists 断言的 expected 必须是布尔值。'})
        return attrs


class UniqueVariableSerializer(StrictSerializer):
    name = serializers.CharField(max_length=64)
    prefix = serializers.CharField(max_length=200, allow_blank=True, default='')

    def validate_name(self, value):
        if not _VARIABLE_NAME_RE.fullmatch(value):
            raise serializers.ValidationError('变量名只能使用字母、数字和下划线，且不能以数字开头。')
        return value

    def validate_prefix(self, value):
        if _CONTROL_RE.search(value):
            raise serializers.ValidationError('唯一值前缀不能包含控制字符。')
        return value


class PlanStepSerializer(StrictSerializer):
    name = serializers.CharField(max_length=200)
    phase = serializers.ChoiceField(choices=('setup', 'main'))
    method = serializers.ChoiceField(choices=ALLOWED_HTTP_METHODS)
    path = serializers.CharField(max_length=2048)
    query = serializers.DictField(default=dict)
    headers = serializers.DictField(default=dict)
    body_type = serializers.ChoiceField(choices=('none', 'json', 'form', 'raw'))
    body = serializers.JSONField(allow_null=True)
    extract = PlanExtractSerializer(many=True, default=list)
    assertions = PlanAssertionSerializer(many=True, allow_empty=False)

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
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '?' in value or '#' in value:
            raise serializers.ValidationError('path 不能是绝对 URL，也不能包含查询或片段。')
        if any(unquote(segment).casefold() in {'.', '..'} for segment in value.split('/')):
            raise serializers.ValidationError('path 不能包含相对目录段。')
        return value

    def validate_query(self, value):
        if len(value) > 100:
            raise serializers.ValidationError('query 最多包含 100 项。')
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256 or _CONTROL_RE.search(key):
                raise serializers.ValidationError('query 参数名无效。')
            values = item if isinstance(item, list) else [item]
            if not values or any(
                isinstance(part, (dict, list)) or part is not None and not isinstance(part, (str, bool, int, float))
                for part in values
            ):
                raise serializers.ValidationError('query 参数值必须是 JSON 标量或非空标量数组。')
            for part in values:
                _validate_json_value(part)
        return value

    def validate_headers(self, value):
        if len(value) > 50:
            raise serializers.ValidationError('headers 最多包含 50 项。')
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise serializers.ValidationError('header 名称和值必须是字符串。')
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", key)
                or key.lower() in _FORBIDDEN_HEADERS or len(item) > 4096
                or _CONTROL_RE.search(item)
            ):
                raise serializers.ValidationError('header 名称或值无效。')
            normalized[key] = item
        return normalized

    def validate_body(self, value):
        _validate_json_value(value)
        return value

    def validate(self, attrs):
        body_type = attrs.get('body_type')
        body = attrs.get('body')
        if body_type == 'none' and body is not None:
            raise serializers.ValidationError({'body': 'body_type=none 时 body 必须为 null。'})
        if body_type == 'form' and not isinstance(body, dict):
            raise serializers.ValidationError({'body': 'form 请求体必须是 JSON 对象。'})
        if body_type == 'raw' and not isinstance(body, str):
            raise serializers.ValidationError({'body': 'raw 请求体必须是字符串。'})
        if body_type == 'form':
            self.validate_query(body)
        return attrs


class PerformancePlanSerializer(StrictModelSerializer):
    target_id = StrictPrimaryKeyRelatedField(
        source='target', queryset=PerformanceTarget.objects.none(),
    )
    users = StrictIntegerField(min_value=1, max_value=MAX_USERS, default=1)
    spawn_rate = StrictFloatField(min_value=0.000001, max_value=MAX_SPAWN_RATE, default=1)
    duration_seconds = StrictIntegerField(min_value=1, max_value=MAX_DURATION_SECONDS, default=30)
    wait_seconds = StrictFloatField(min_value=0.1, max_value=60, default=1)
    connect_timeout_seconds = StrictIntegerField(
        min_value=1, max_value=MAX_CONNECT_TIMEOUT_SECONDS,
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )
    read_timeout_seconds = StrictIntegerField(
        min_value=1, max_value=MAX_READ_TIMEOUT_SECONDS,
        default=DEFAULT_READ_TIMEOUT_SECONDS,
    )
    variables = serializers.DictField(default=dict)
    unique_variables = UniqueVariableSerializer(many=True, default=list)
    steps = PlanStepSerializer(many=True, allow_empty=False)

    class Meta:
        model = PerformancePlan
        fields = (
            'id', 'name', 'description', 'target_id', 'users', 'spawn_rate',
            'duration_seconds', 'wait_seconds', 'connect_timeout_seconds',
            'read_timeout_seconds', 'variables', 'unique_variables', 'steps',
            'created_at', 'updated_at',
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

    def validate_variables(self, value):
        if len(value) > 100:
            raise serializers.ValidationError('variables 最多包含 100 项。')
        for key, item in value.items():
            if not isinstance(key, str) or not _VARIABLE_NAME_RE.fullmatch(key):
                raise serializers.ValidationError('variables 的变量名无效。')
            _validate_json_value(item)
        return value

    @staticmethod
    def _references(value):
        if isinstance(value, str):
            return set(_VARIABLE_REF_RE.findall(value))
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(PerformancePlanSerializer._references(item))
            return result
        if isinstance(value, dict):
            result = set()
            for key, item in value.items():
                result.update(_VARIABLE_REF_RE.findall(key))
                result.update(PerformancePlanSerializer._references(item))
            return result
        return set()

    def _validate_data_flow(self, variables, unique_variables, steps):
        fixed = set(variables)
        unique = [item['name'] for item in unique_variables]
        if len(unique) != len(set(unique)):
            raise serializers.ValidationError({'unique_variables': '唯一变量名不能重复。'})
        if fixed.intersection(unique):
            raise serializers.ValidationError({'unique_variables': '固定变量与唯一变量不能同名。'})
        extracted = set()
        setup_available = set(fixed)
        setup_outputs = set()
        main_available = None
        main_seen = False
        main_count = 0
        for index, step in enumerate(steps):
            phase = step['phase']
            if phase == 'setup' and main_seen:
                raise serializers.ValidationError({'steps': '所有 setup 步骤必须位于 main 步骤之前。'})
            if phase == 'main':
                main_seen = True
                main_count += 1
                if main_available is None:
                    main_available = fixed | set(unique) | setup_outputs
                available = main_available
            else:
                available = setup_available
            request_values = {
                'path': step['path'], 'query': step['query'], 'headers': step['headers'],
                'body': step['body'],
            }
            undefined = sorted(self._references(request_values) - available)
            if undefined:
                raise serializers.ValidationError({
                    'steps': f'第 {index + 1} 步引用了尚未定义的变量：{", ".join(undefined)}。',
                })
            names = [item['name'] for item in step['extract']]
            if len(names) != len(set(names)) or (set(names) & (fixed | set(unique) | extracted)):
                raise serializers.ValidationError({'steps': f'第 {index + 1} 步提取变量名重复或已被占用。'})
            extracted.update(names)
            if phase == 'setup':
                setup_available.update(names)
                setup_outputs.update(names)
            else:
                main_available.update(names)
        if not main_count:
            raise serializers.ValidationError({'steps': '计划至少需要一个 main 步骤。'})

    def validate(self, attrs):
        target = attrs.get('target') or (self.instance.target if self.instance else None)
        steps = attrs.get('steps') or (self.instance.steps if self.instance else [])
        variables = attrs.get('variables', getattr(self.instance, 'variables', {}))
        unique_variables = attrs.get(
            'unique_variables', getattr(self.instance, 'unique_variables', []),
        )
        self._validate_data_flow(variables, unique_variables, steps)
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
            'connect_timeout_seconds': attrs.get(
                'connect_timeout_seconds',
                getattr(self.instance, 'connect_timeout_seconds', DEFAULT_CONNECT_TIMEOUT_SECONDS),
            ),
            'read_timeout_seconds': attrs.get(
                'read_timeout_seconds',
                getattr(self.instance, 'read_timeout_seconds', DEFAULT_READ_TIMEOUT_SECONDS),
            ),
            'variables': variables,
            'unique_variables': unique_variables,
            'steps': steps,
        }
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_REQUEST_BYTES:
            raise serializers.ValidationError('计划 JSON 内容过大。')
        return attrs


class PerformanceNodeSerializer(StrictModelSerializer):
    status = serializers.SerializerMethodField()
    registered_at = serializers.DateTimeField(source='enrollment_consumed_at', read_only=True)
    active_run_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PerformanceNode
        fields = (
            'id', 'name', 'network_mode', 'status', 'active_run_count', 'last_seen_at',
            'agent_version', 'engine_version', 'protocol_version', 'resources',
            'registered_at', 'created_at',
        )
        read_only_fields = (
            'id', 'status', 'active_run_count', 'last_seen_at', 'agent_version', 'engine_version',
            'protocol_version', 'resources', 'registered_at', 'created_at',
        )

    def get_status(self, obj):
        return obj.status_at(self.context.get('current_time'))

    def validate_name(self, value):
        value = value.strip()
        if not value or _CONTROL_RE.search(value):
            raise serializers.ValidationError('名称不能为空或包含控制字符。')
        return value

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
    node_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=MAX_NODES_PER_RUN,
    )
    request_id = serializers.UUIDField()
    mode = serializers.ChoiceField(choices=('validation', 'load'), default='load')

    def validate(self, attrs):
        node_ids = attrs['node_ids']
        if len(node_ids) != len(set(node_ids)):
            raise serializers.ValidationError({'node_ids': '不能包含重复节点。'})
        if attrs['mode'] == PerformanceRun.Mode.VALIDATION and len(node_ids) != 1:
            raise serializers.ValidationError({'node_ids': '单用户验证必须且只能选择一个节点。'})
        return attrs


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if not isinstance(data, bool):
            self.fail('invalid')
        return data


class NodeRevokeSerializer(StrictSerializer):
    confirm_stop = StrictBooleanField(required=False, default=False)


class NodeReinstallSerializer(StrictSerializer):
    confirm_old_container_removed = StrictBooleanField(required=True)

    def validate_confirm_old_container_removed(self, value):
        if value is not True:
            raise serializers.ValidationError('必须确认旧容器已停止并删除。')
        return value


class PerformanceRunNodeListSerializer(serializers.ModelSerializer):
    node_id = serializers.UUIDField(read_only=True)
    node_name = serializers.SerializerMethodField()
    node_name_source = serializers.SerializerMethodField()
    latest_metrics = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceRunNode
        fields = (
            'node_id', 'node_name', 'node_name_source', 'assigned_users', 'status',
            'reason_code', 'reason', 'latest_metrics',
        )
        read_only_fields = fields

    def get_node_name(self, obj):
        return obj.node_name or obj.node.name

    def get_node_name_source(self, obj):
        return 'snapshot' if obj.node_name else 'current_node'

    def get_latest_metrics(self, obj):
        # Step request/response evidence is only exposed through the bounded,
        # REPORT-protected validation_steps detail field on the parent run.
        return {
            key: value for key, value in (obj.latest_metrics or {}).items()
            if key != 'validation_steps'
        }


class PerformanceRunNodeDetailSerializer(PerformanceRunNodeListSerializer):
    validation_run_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta(PerformanceRunNodeListSerializer.Meta):
        fields = PerformanceRunNodeListSerializer.Meta.fields + (
            'node_agent_version', 'node_protocol_version', 'node_engine_version',
            'validation_key', 'validation_run_id', 'node_report_seq', 'ready_at',
            'started_at', 'stopped_at', 'last_command_at', 'metrics_samples',
        )


class PerformanceRunListSerializer(serializers.ModelSerializer):
    plan_name = serializers.SerializerMethodField()
    node_count = serializers.SerializerMethodField()
    nodes = serializers.SerializerMethodField()
    validation_status = serializers.SerializerMethodField()
    latest_metrics = serializers.SerializerMethodField()

    class Meta:
        model = PerformanceRun
        fields = (
            'id', 'plan_id', 'plan_name', 'node_count', 'nodes', 'mode',
            'status', 'validation_status',
            'created_at', 'started_at', 'finished_at', 'reason_code', 'reason',
            'latest_metrics',
        )
        read_only_fields = fields

    def get_plan_name(self, obj):
        return (obj.snapshot or {}).get('plan_name', '')

    def get_node_count(self, obj):
        return len(self._participants(obj))

    def get_nodes(self, obj):
        return PerformanceRunNodeListSerializer(self._participants(obj), many=True).data

    @staticmethod
    def _participants(obj):
        return list(obj.participants.all())

    def get_latest_metrics(self, obj):
        # Response bodies belong only to the REPORT-protected detail endpoint.
        return {key: value for key, value in (obj.latest_metrics or {}).items() if key != 'validation_steps'}

    def get_validation_status(self, obj):
        if obj.mode != PerformanceRun.Mode.VALIDATION:
            return 'not_applicable'
        if obj.status not in PerformanceRun.TERMINAL_STATUSES:
            return 'pending'
        if obj.status != PerformanceRun.Status.COMPLETED or not (obj.latest_metrics or {}).get('validation_passed'):
            return 'failed'
        if obj.plan_id is None:
            return 'stale'
        from .run_services import validation_key_for
        participants = self._participants(obj)
        if len(participants) != 1:
            return 'stale'
        node = participants[0].node
        if (
            node.enrollment_consumed_at is None
            or obj.created_at < node.enrollment_consumed_at
        ):
            return 'stale'
        try:
            current = validation_key_for(obj.plan, node)
        except (TypeError, ValueError):
            return 'stale'
        return 'passed' if current == obj.validation_key else 'stale'


class PerformanceRunDetailSerializer(PerformanceRunListSerializer):
    validation_steps = serializers.SerializerMethodField()

    def get_validation_steps(self, obj):
        if obj.mode != PerformanceRun.Mode.VALIDATION:
            return []
        from .controller import bounded_validation_steps
        return bounded_validation_steps((obj.latest_metrics or {}).get('validation_steps', []), obj.snapshot)

    def get_nodes(self, obj):
        return PerformanceRunNodeDetailSerializer(self._participants(obj), many=True).data

    class Meta(PerformanceRunListSerializer.Meta):
        fields = PerformanceRunListSerializer.Meta.fields + ('metrics_samples', 'snapshot', 'validation_steps')
