from rest_framework import serializers
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .credentials import identity_summary
from .models import SecurityPlan, SecurityTask


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({'non_field_errors': ['请求内容必须是对象。']})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({'non_field_errors': ['请求包含不支持的字段。']})
        return super().to_internal_value(data)


class PlanWriteSerializer(StrictSerializer):
    name = serializers.CharField(max_length=200)
    config = serializers.JSONField()
    identities = serializers.JSONField(required=False)

    def validate_config(self, value):
        from .validation import validate_config
        try:
            return validate_config(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None

    def validate_identities(self, value):
        from .validation import validate_identities
        try:
            return validate_identities(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from None


class PlanSerializer(serializers.ModelSerializer):
    identities = serializers.SerializerMethodField()
    project_id = serializers.IntegerField(read_only=True)

    def get_identities(self, obj):
        try:
            return identity_summary(obj.identities_ciphertext)
        except Exception:
            # A rotated encryption key never causes a credential/error-text leak.
            return {'unavailable': True} if obj.identities_ciphertext else {}

    class Meta:
        model = SecurityPlan
        fields = ('id', 'project_id', 'name', 'config', 'identities', 'cases', 'revision', 'created_at', 'updated_at')


class PlanningCreateSerializer(StrictSerializer):
    request_id = serializers.UUIDField()
    model_config_id = serializers.IntegerField(min_value=1, required=False, allow_null=True, default=None)


class ExecutionCreateSerializer(StrictSerializer):
    request_id = serializers.UUIDField()
    case_ids = serializers.ListField(child=serializers.CharField(max_length=100), min_length=1, max_length=80)

    def validate_case_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('检查项不可重复。')
        return value


class TaskListSerializer(serializers.ModelSerializer):
    plan_name = serializers.SerializerMethodField()
    plan_id = serializers.UUIDField(read_only=True)
    project_id = serializers.IntegerField(read_only=True)

    def get_plan_name(self, obj):
        return obj.snapshot.get('plan_name', '')

    class Meta:
        model = SecurityTask
        fields = ('id', 'project_id', 'plan_id', 'plan_name', 'kind', 'request_id', 'status',
                  'model_config_id', 'progress', 'cancel_requested', 'error_code', 'error_message',
                  'started_at', 'heartbeat_at', 'finished_at', 'deadline_at', 'created_at', 'updated_at')


class TaskDetailSerializer(TaskListSerializer):
    snapshot = serializers.SerializerMethodField()

    def get_snapshot(self, obj):
        snapshot = obj.snapshot if isinstance(obj.snapshot, dict) else {}
        config = snapshot.get('config', {})
        endpoints = []
        for item in config.get('endpoints', []):
            try:
                parts = urlsplit(item.get('url', ''))
                url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                  urlencode([(key, '[已隐藏]') for key, _ in parse_qsl(parts.query, keep_blank_values=True)]), ''))
            except ValueError:
                url = '[地址不可用]'
            endpoints.append({
                'name': item.get('name', ''), 'url': url, 'method': item.get('method', ''),
                'auth_required': item.get('auth_required', False), 'owner_only_a': item.get('owner_only_a', False),
                'header_names': list(item.get('headers', {})),
                'proof': {'configured': bool(item.get('proof'))},
            })
        safe_config = {key: config[key] for key in ('allowed_origins', 'allow_private_network', 'checks',
                                                   'max_requests', 'rate_per_second', 'timeout_seconds') if key in config}
        safe_config['endpoints'] = endpoints
        return {'config': safe_config, 'cases': snapshot.get('cases', []),
                'plan_name': snapshot.get('plan_name', ''), 'plan_revision': snapshot.get('plan_revision')}

    class Meta(TaskListSerializer.Meta):
        fields = TaskListSerializer.Meta.fields + ('snapshot', 'result')
