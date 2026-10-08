import math

from rest_framework import serializers

from .models import PerformanceAnalysis
from .serializers import StrictIntegerField, StrictSerializer


class StrictFiniteFloatField(serializers.FloatField):
    def to_internal_value(self, data):
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            self.fail('invalid')
        try:
            value = super().to_internal_value(data)
        except (OverflowError, ValueError, TypeError):
            self.fail('invalid')
        if not math.isfinite(value):
            self.fail('invalid')
        return value


class PerformanceAnalysisTargetsSerializer(StrictSerializer):
    p95_ms = StrictFiniteFloatField(required=False, min_value=0)
    error_rate_percent = StrictFiniteFloatField(required=False, min_value=0, max_value=100)
    rps_min = StrictFiniteFloatField(required=False, min_value=0)

    def validate_p95_ms(self, value):
        if value <= 0:
            raise serializers.ValidationError('p95_ms 必须大于 0。')
        return value

    def validate_rps_min(self, value):
        if value <= 0:
            raise serializers.ValidationError('rps_min 必须大于 0。')
        return value


class PerformanceAnalysisCreateSerializer(StrictSerializer):
    request_id = serializers.UUIDField()
    model_config_id = StrictIntegerField(min_value=1, max_value=9223372036854775807)
    analysis_type = serializers.ChoiceField(
        choices=PerformanceAnalysis.AnalysisType.choices,
        default=PerformanceAnalysis.AnalysisType.LOAD_SUMMARY,
    )
    comparison_run_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    targets = PerformanceAnalysisTargetsSerializer(required=False)

    def validate(self, attrs):
        analysis_type = attrs['analysis_type']
        comparison_run_id = attrs.get('comparison_run_id')
        targets = attrs.get('targets') or {}
        if analysis_type == PerformanceAnalysis.AnalysisType.LOAD_COMPARISON:
            if comparison_run_id is None:
                raise serializers.ValidationError({
                    'comparison_run_id': '历史压测对比必须选择基线运行。',
                })
        elif comparison_run_id is not None:
            raise serializers.ValidationError({
                'comparison_run_id': '当前分析类型不支持基线运行。',
            })
        if analysis_type != PerformanceAnalysis.AnalysisType.LOAD_SUMMARY and targets:
            raise serializers.ValidationError({
                'targets': '当前分析类型不支持性能目标。',
            })
        return attrs


class PerformanceAnalysisListQuerySerializer(StrictSerializer):
    analysis_type = serializers.ChoiceField(
        choices=PerformanceAnalysis.AnalysisType.choices, required=False,
    )
    comparison_run_id = serializers.UUIDField(required=False)


class PerformanceAnalysisSerializer(serializers.ModelSerializer):
    run_id = serializers.UUIDField(read_only=True)
    comparison_run_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = PerformanceAnalysis
        fields = (
            'id', 'run_id', 'analysis_type', 'comparison_run_id',
            'status', 'model_info', 'targets', 'result',
            'timeout_seconds', 'progress', 'error_code', 'error_message',
            'created_at', 'started_at', 'finished_at',
        )
        read_only_fields = fields
