"""Strict request contracts for performance browser discovery endpoints."""
from rest_framework import serializers

from api_testing.serializers import (
    BrowserDiscoveryCreateSerializer,
    BrowserDiscoveryOriginDecisionSerializer,
)
from .serializers import StrictIntegerField, StrictSerializer


class _StrictExistingFieldsMixin:
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({
                'non_field_errors': ['请求包含不支持的字段。'],
            })
        return super().to_internal_value(data)


class PerformanceDiscoveryCreateSerializer(
    _StrictExistingFieldsMixin, BrowserDiscoveryCreateSerializer,
):
    pass


class PerformanceDiscoveryOriginSerializer(
    _StrictExistingFieldsMixin, BrowserDiscoveryOriginDecisionSerializer,
):
    pass


class PerformanceDiscoveryDraftSerializer(StrictSerializer):
    version = StrictIntegerField(min_value=1)
    record_ids = serializers.ListField(
        child=StrictIntegerField(min_value=1), allow_empty=False, max_length=500,
    )
    target_id = StrictIntegerField(min_value=1)

    def validate_record_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError('record_ids 不能重复。')
        return value


class PerformanceDiscoveryRetrySerializer(StrictSerializer):
    version = StrictIntegerField(min_value=1)
