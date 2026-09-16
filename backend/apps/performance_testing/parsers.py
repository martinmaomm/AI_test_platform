import codecs

from django.conf import settings
from rest_framework.exceptions import ParseError, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.utils import json

from .constants import MAX_REQUEST_BYTES


class LimitedJSONParser(JSONParser):
    """JSON-only parser with a hard byte cap independent of Content-Length."""

    max_bytes = MAX_REQUEST_BYTES

    def parse(self, stream, media_type=None, parser_context=None):
        parser_context = parser_context or {}
        request = parser_context.get('request')
        raw_length = request.META.get('CONTENT_LENGTH') if request is not None else None
        try:
            if raw_length not in (None, ''):
                parsed_length = int(raw_length)
                if parsed_length < 0:
                    raise ParseError('无效的 Content-Length。')
                if parsed_length > self.max_bytes:
                    raise ValidationError('请求内容过大。')
        except (TypeError, ValueError):
            raise ParseError('无效的 Content-Length。')

        raw = stream.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise ValidationError('请求内容过大。')
        encoding = parser_context.get('encoding') or settings.DEFAULT_CHARSET
        try:
            decoded = codecs.decode(raw, encoding)
            parse_constant = json.strict_constant if self.strict else None
            payload = json.loads(decoded, parse_constant=parse_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ParseError(f'JSON 格式无效：{exc}')
        if not isinstance(payload, dict):
            raise ValidationError('请求 JSON 顶层必须是对象。')
        return payload
