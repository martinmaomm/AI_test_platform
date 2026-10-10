"""Permission checked, bounded copies of read-only endpoints without credentials."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db.models import Q

from api_testing.models import APIEndpoint, BrowserDiscoveryRecord
from projects.access import READ, REPORT, get_project_for_user, projects_for_user

_SECRET = re.compile(r'auth|token|password|passwd|secret|session|cookie|api.?key|signature|credential|access.?key', re.I)


def source_projects(user):
    projects = projects_for_user(user).filter(project_type='api')
    result = []
    for project in projects.order_by('id')[:200]:
        from django.http import Http404
        from rest_framework.exceptions import PermissionDenied
        try:
            get_project_for_user(project.pk, user, REPORT, expected_project_type='api')
        except (Http404, PermissionDenied):
            continue
        result.append({'id': project.pk, 'name': project.name})
    return result


def _clean_url(url):
    if not isinstance(url, str) or len(url) > 2000 or any(marker in url.lower() for marker in ('{', '}', '<', '>', '[redacted]', '%7b', '%7d')):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.fragment:
            return None
        parts.port
    except ValueError:
        return None
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not _SECRET.search(key) and value not in ('***', '[REDACTED]')]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def _draft(source_id, name, url, method, headers=None):
    clean = _clean_url(url)
    if clean is None or method not in ('GET', 'HEAD'):
        return None
    from .validation import validate_headers
    safe_headers = {}
    for key, value in (headers or {}).items():
        try:
            candidate = validate_headers({key: value})
            validate_headers({**safe_headers, **candidate})
        except ValueError:
            continue
        safe_headers.update(candidate)
    return {'source_id': source_id, 'name': str(name or method)[:120], 'url': clean, 'method': method,
            'headers': safe_headers, 'auth_required': False, 'owner_only_a': False, 'proof': None}


def _document_url(endpoint):
    if endpoint.path.startswith(('http://', 'https://')):
        return endpoint.path
    metadata = endpoint.spec.metadata if isinstance(endpoint.spec.metadata, dict) else {}
    servers = metadata.get('servers')
    base = ''
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        base = servers[0].get('url', '')
    if not base and isinstance(metadata.get('host'), str):
        schemes = metadata.get('schemes', ['https'])
        scheme = schemes[0] if isinstance(schemes, list) and schemes else 'https'
        base = f'{scheme}://{metadata["host"]}{metadata.get("basePath", "")}'
    return base.rstrip('/') + '/' + endpoint.path.lstrip('/') if isinstance(base, str) and base else ''


def source_endpoints(project_id, user):
    project = get_project_for_user(project_id, user, READ, expected_project_type='api')
    get_project_for_user(project_id, user, REPORT, expected_project_type='api')
    result, skipped, removed_headers = [], 0, set()
    endpoints = APIEndpoint.objects.filter(spec__project=project).exclude(
        Q(spec__spec_type='browser_capture') & ~Q(spec__source_task__owner=user),
    ).select_related('spec').order_by('-id')[:100]
    for item in endpoints:
        draft = _draft(f'document:{item.pk}', item.summary or item.path, _document_url(item), item.method.upper())
        if draft:
            result.append(draft)
        else:
            skipped += 1
    records = BrowserDiscoveryRecord.objects.filter(
        task__project=project, task__owner=user, task__status__in=('completed', 'partial', 'failed', 'cancelled'),
        is_eligible=True, method__in=('GET', 'HEAD'),
    ).order_by('-id')[:100]
    for record in records:
        if len(result) >= 100:
            break
        summary = record.public_summary if isinstance(record.public_summary, dict) else {}
        observed = summary.get('observed_request')
        observed = observed if isinstance(observed, dict) else {}
        url = observed.get('url') or record.origin.rstrip('/') + '/' + record.path.lstrip('/')
        if not observed.get('url') and '?' not in url:
            query = observed.get('query', [])
            if isinstance(query, list):
                pairs = [(item['name'], item['value']) for item in query if isinstance(item, dict)
                         and isinstance(item.get('name'), str) and isinstance(item.get('value'), (str, int, float))
                         and not _SECRET.search(item['name']) and str(item['value']) not in ('***', '[REDACTED]')]
                if pairs:
                    url += '?' + urlencode(pairs)
        headers = observed.get('headers')
        draft = _draft(f'discovery:{record.pk}', record.path, url, record.method,
                       headers if isinstance(headers, dict) else {})
        if draft:
            if isinstance(headers, dict):
                removed_headers.update(key for key in headers if isinstance(key, str) and key not in draft['headers']
                                       and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key))
            result.append(draft)
        else:
            skipped += 1
    warnings = ['导入结果是草稿；已移除认证请求头和可识别的认证参数，请核对范围、参数及账号后保存。']
    if removed_headers:
        warnings.append(f'已移除 {len(removed_headers)} 种认证或连接请求头：' + '、'.join(sorted(removed_headers)[:30]) + '。')
    if skipped:
        warnings.append(f'已跳过 {skipped} 个非只读、缺少完整地址或含模板的端点。')
    if len(result) >= 100:
        warnings.append('最多返回 100 个候选端点。')
    return {'endpoints': result[:100], 'warnings': warnings}
