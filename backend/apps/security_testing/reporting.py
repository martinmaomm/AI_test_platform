"""Render saved, sanitized security evidence as a standalone offline report."""
from collections import Counter
from html import escape
import math

from django.utils import timezone


STATUS_LABELS = {
    'queued': '排队中', 'running': '运行中', 'completed': '已完成',
    'failed': '执行失败', 'cancelled': '已取消',
    'passed': '本项通过', 'confirmed': '已确认', 'suspected': '疑似问题',
    'inconclusive': '未验证', 'error': '检查出错', 'skipped': '已跳过',
}
CHECK_LABELS = {
    'authentication': '认证校验', 'authorization': '资源越权',
    'information': '响应信息泄露', 'configuration': '安全配置',
}
RESULT_STATUSES = ('confirmed', 'suspected', 'passed', 'inconclusive', 'error', 'skipped')
SAFE_RESPONSE_HEADERS = {
    'content-type', 'content-length', 'strict-transport-security',
    'content-security-policy', 'x-content-type-options', 'x-frame-options',
    'referrer-policy', 'permissions-policy', 'access-control-allow-origin',
    'access-control-allow-credentials', 'cache-control',
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value, limit=2000):
    return escape(value[:limit], quote=True) if isinstance(value, str) else ''


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return '—'
    return f'{value:g}'


def _date(value):
    if value is None:
        return '—'
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return _text(value.strftime('%Y-%m-%d %H:%M:%S'))


def _evidence(value):
    """Project the runtime's persisted evidence; never export arbitrary fields."""
    rows = []
    for raw in _list(value)[:8]:
        item = _dict(raw)
        variant = _text(item.get('variant_label') or item.get('variant'), 100)
        status = _number(item.get('status_code'))
        proof = {True: '匹配', False: '不匹配', None: '未配置或未验证'}.get(item.get('proof_match'), '未验证')
        headers = '\n'.join(
            f'{key}: {val}' for key, val in _dict(item.get('response_headers')).items()
            if isinstance(key, str) and key.lower() in SAFE_RESPONSE_HEADERS and isinstance(val, str)
        )
        rows.append(f'''<article class="evidence"><h4>{variant or '请求证据'}</h4>
<p class="url">{_text(item.get('method'), 12)} {_text(item.get('url'), 2000)}</p>
<p>HTTP 状态：{status}　耗时：{_number(item.get('elapsed_ms'))} ms　读取：{_number(item.get('response_bytes'))} 字节　受保护资源标记：{proof}</p>
<pre>{_text(headers, 6000)}</pre><pre>{_text(item.get('response_preview'), 16000)}</pre>
{('<p class="muted">响应已截断，仅展示有限证据。</p>' if item.get('truncated') is True else '')}
{('<p>错误分类：' + _text(item.get('error_code'), 100) + '</p>' if item.get('error_code') else '')}</article>''')
    return ''.join(rows) or '<p class="muted">本项没有请求证据，请结合检查状态判断。</p>'


def render_report(task):
    """Read only saved execution data: no replay, AI call, or credential access."""
    snapshot, result = _dict(task.snapshot), _dict(task.result)
    config = _dict(snapshot.get('config'))
    items = [_dict(item) for item in _list(result.get('items'))[:80]]
    counts = Counter(item.get('status') for item in items if isinstance(item.get('status'), str))
    title = _text(snapshot.get('plan_name'), 200) or '安全测试'
    parts = [f'''<header><p class="eyebrow">网站与 API 安全测试 · 离线报告</p>
<h1>{title}</h1><p>任务 {_text(str(task.pk), 80)} · {STATUS_LABELS.get(task.status, '未知状态')}</p>
<p class="muted">创建：{_date(task.created_at)}　开始：{_date(task.started_at)}　结束：{_date(task.finished_at)}</p>
<p class="muted">导出：{_date(timezone.now())}。此文件可离线打开，无需登录平台。</p></header>''']
    if task.status != 'completed':
        parts.append('<section class="notice"><h2>执行未完整完成</h2><p>' +
                     _text(getattr(task, 'error_message', ''), 500) +
                     '</p><p>本报告仅包含已保存的结果；未执行或未保存的检查不能视为通过。</p></section>')
    parts.append('<section><h2>结果概览</h2><div class="metrics">' + ''.join(
        f'<article><span>{STATUS_LABELS[key]}</span><strong>{counts[key]}</strong></article>'
        for key in RESULT_STATUSES
    ) + '</div><p>“本项通过”仅表示本次检查满足判定条件；未发现问题不代表网站整体安全。</p></section>')
    checks = '、'.join(CHECK_LABELS[check] for check in _list(config.get('checks')) if isinstance(check, str) and check in CHECK_LABELS)
    parts.append(f'''<section><h2>检查范围与执行限制</h2>
<p>接口数：{len(_list(config.get('endpoints')))}　选中检查：{len(_list(snapshot.get('cases')))}　已保存结果：{len(items)}</p>
<p>检查类型：{checks or '未记录'}</p><p>请求预算：{_number(config.get('max_requests'))} 次　实际请求：{_number(_dict(result.get('summary')).get('requests_sent'))} 次</p>
<p>请求速率上限：{_number(config.get('rate_per_second'))} 次/秒　单请求超时：{_number(config.get('timeout_seconds'))} 秒</p>
<p class="muted">第一版仅执行选定的 GET/HEAD 请求，不自动登录、不跟随重定向。报告不包含账号凭据和原始请求头；响应预览已脱敏，可能不包含完整业务数据。</p></section>''')
    parts.append('<section><h2>检查明细与修复建议</h2>')
    for item in items:
        status = item.get('status') if item.get('status') in RESULT_STATUSES else 'inconclusive'
        check = item.get('check') if isinstance(item.get('check'), str) else ''
        parts.append(f'''<article class="finding {status}"><h3>{_text(item.get('title'), 200) or CHECK_LABELS.get(check, '检查项')}
<small>{STATUS_LABELS[status]}</small></h3><p class="muted">{CHECK_LABELS.get(check, '其他检查')} · {_text(item.get('case_id'), 100)}</p>
<p class="prose">{_text(item.get('summary'), 3000)}</p><p class="prose">建议：{_text(item.get('recommendation'), 3000) or '请结合证据复核。'}</p>
{_evidence(item.get('evidence'))}</article>''')
    if not items:
        parts.append('<p>没有已保存的检查结果，不能据此得出安全结论。</p>')
    parts.append('</section><section><h2>判定口径与局限</h2><ul><li>仅 HTTP 200、响应相似或安全响应头缺失，不能单独证明存在可利用漏洞。</li>'
                 '<li>认证和越权的已确认结果依赖配置的保护范围、账号基准及受保护资源标记；配置错误会影响结论。</li>'
                 '<li>疑似问题需人工复核；未验证、检查出错和已跳过均不等同于通过。</li>' + ''.join(
                     '<li>' + _text(value, 2000) + '</li>' for value in _list(result.get('limitations'))[:40] if isinstance(value, str)
                 ) + '</ul></section>')
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>安全测试报告</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#182536;font:15px/1.65 system-ui,-apple-system,"PingFang SC",sans-serif}
main{max-width:1100px;margin:32px auto;padding:0 24px}header,section{background:#fff;border:1px solid #dce4ed;border-radius:14px;padding:24px;margin-bottom:20px}
h1{font-size:30px;margin:6px 0}h2{font-size:22px;margin-top:0}h3{font-size:18px}h4{margin:0 0 8px}.eyebrow{font-size:13px;color:#346a98;letter-spacing:.06em}.muted,small{color:#617083}
small{font-size:13px;margin-left:12px}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.metrics article{background:#f3f6fa;padding:16px;border-radius:8px}.metrics span,.metrics strong{display:block}.metrics strong{font-size:30px}
.finding{border-top:1px solid #dce4ed;padding:8px 0 24px}.finding.confirmed h3{color:#b42318}.finding.suspected h3{color:#986000}.evidence{background:#f6f8fb;border:1px solid #e1e7ef;border-radius:8px;padding:16px;margin:12px 0}
p,li,.url{overflow-wrap:anywhere}.prose,pre{white-space:pre-wrap;overflow-wrap:anywhere}pre{font:13px/1.6 ui-monospace,monospace;background:#fff;padding:12px;border-radius:6px;max-height:none}pre:empty{display:none}.notice{border-left:5px solid #d39d34}
@media(max-width:720px){main{padding:0 12px;margin:12px auto}header,section{padding:18px}.metrics{grid-template-columns:repeat(2,1fr)}}
@media print{body{background:#fff}main{max-width:none;margin:0;padding:0}header,section{border-radius:0}.evidence{break-inside:avoid}}
</style></head><body><main>''' + ''.join(parts) + '</main></body></html>'
