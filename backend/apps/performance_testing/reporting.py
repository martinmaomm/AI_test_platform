"""Self-contained, script-free performance reports built from allowlisted data."""
from html import escape

from django.utils import timezone

from .acceptance import TARGETS, run_acceptance, validate_targets
from .analysis_data import _dict, _list, build_analysis_input, number
from .failure_diagnosis import build_failure_diagnosis


STATUS_LABELS = {
    'completed': '已完成', 'failed': '失败', 'cancelled': '已取消', 'incomplete': '数据不完整',
    'stopped': '已停止', 'lost': '已失联', 'running': '运行中', 'ready': '就绪',
    'queued': '排队中', 'preparing': '准备中', 'stopping': '停止中',
    'met': '达标', 'not_met': '未达标', 'not_configured': '未配置验收标准',
    'insufficient_data': '数据不足', 'not_applicable': '不适用', 'pending': '待验收',
}
ERROR_LABELS = {
    'connect_timeout': '连接超时', 'read_timeout': '响应超时', 'dns_error': '域名解析失败',
    'tls_error': 'TLS 错误', 'connection_error': '连接失败', 'undefined_variable': '变量未定义',
    'request_render_error': '请求渲染失败', 'response_too_large': '响应过大',
    'extraction_missing': '变量提取失败', 'assertion_failed': '断言失败', 'type_mismatch': '类型不匹配',
    'unsupported_comparator': '不支持的比较方式', 'missing_value': '值缺失', 'invalid_json': 'JSON 无效',
    'other': '其他错误',
}
METRIC_COLUMNS = [('requests', '请求数'), ('failures', '失败请求数'),
                  ('error_rate_percent', '错误率（%）'), ('rps', '平均吞吐量（次/秒）'),
                  ('avg_response_time', '平均响应（ms）'), ('p95', 'P95（ms）'), ('p99', 'P99（ms）')]


def _text(value, limit=4000):
    return escape(value[:limit], quote=True) if isinstance(value, str) else ''


def _numeric(value):
    value = number(value)
    return '—' if value is None else (str(value) if type(value) is int else f'{value:,.3f}'.rstrip('0').rstrip('.'))


def _date(value):
    return _text(timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S %Z')) if value else '—'


def _table(headers, rows):
    if not rows:
        return '<p class="muted">暂无数据</p>'
    head = ''.join(f'<th scope="col">{_text(item)}</th>' for item in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _chart(title, rows, x_key, y_key, unit, x_label):
    # Only finite, nonnegative numerical coordinates enter SVG attributes.
    points = [(number(_dict(row).get(x_key)), number(_dict(row).get(y_key))) for row in rows]
    known = [(x, y) for x, y in points if x is not None and y is not None]
    if not known:
        return f'<article class="chart"><h3>{_text(title)}</h3><p class="muted">暂无有效采样</p></article>'
    max_x = max(x for x, _ in known) or 1
    max_y = max(y for _, y in known) or 1
    paths, path = [], []
    for x, y in points:
        if x is None or y is None:
            if path:
                paths.append(path)
            path = []
        else:
            path.append((48 + x / max_x * 624, 198 - y / max_y * 150))
    if path:
        paths.append(path)
    # Gaps in retained samples stay gaps; never imply interpolation across missing values.
    graphics = ''.join(
        '<polyline fill="none" stroke="#2563eb" stroke-width="2.5" points="' +
        ' '.join(f'{x:.2f},{y:.2f}' for x, y in segment) + '"/>' +
        ''.join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.5" fill="#2563eb"/>' for x, y in segment)
        for segment in paths
    )
    return f'''<article class="chart"><h3>{_text(title)}</h3>
<svg viewBox="0 0 710 248" role="img" aria-label="{_text(title)}">
<title>{_text(title)}</title><path d="M48 40V198H672" fill="none" stroke="#94a3b8"/>
<text x="48" y="25">{_numeric(max(y for _, y in known))} {_text(unit)}</text>
<text x="28" y="203">0</text><text x="48" y="225">0</text>
<text x="672" y="225" text-anchor="end">{_numeric(max(x for x, _ in known))} {_text(x_label)}</text>
{graphics}</svg></article>'''


def render_report(run):
    """Render a persisted run without dispatching any analysis or replaying requests."""
    try:
        targets = validate_targets(run.acceptance_targets)
    except ValueError:
        targets = {}
    safe = build_analysis_input(run, targets)
    evidence = {item['id']: item['value'] for item in safe['evidence']}
    acceptance = run_acceptance(run)
    snapshot = _dict(run.snapshot)
    plan_name = snapshot.get('plan_name')
    if not isinstance(plan_name, str):
        plan_name = run.plan.name if run.plan else '历史压测计划'
    parts = [f'''<header><p class="eyebrow">性能测试 · 离线报告</p><h1>{_text(plan_name, 200)}</h1>
<p>运行 {_text(str(run.pk))} · 正式压测 · {STATUS_LABELS.get(run.status, '未知状态')}</p>
<p class="muted">创建：{_date(run.created_at)}　开始：{_date(run.started_at)}　结束：{_date(run.finished_at)}</p>
<p class="muted">导出：{_date(timezone.now())}。此文件可离线查看，无需登录平台。</p></header>''']
    config_labels = {'users': '并发用户数', 'spawn_rate': '用户启动速率（人/秒）',
                     'duration_seconds': '计划持续时长（秒）', 'wait_seconds': '循环等待（秒）',
                     'connect_timeout_seconds': '连接超时（秒）', 'read_timeout_seconds': '响应超时（秒）'}
    parts.append('<section><h2>运行配置</h2>' + _table(['参数', '本次运行配置'], [
        [_text(label), _numeric(_dict(evidence.get('load.config')).get(key))] for key, label in config_labels.items()
    ]) + '</section>')
    parts.append('<section><h2>性能验收</h2><p class="verdict">' +
                 STATUS_LABELS.get(acceptance['status'], '数据不足') + '</p>')
    parts.append(_table(['指标', '运行前目标', '实际值', '结论'], [
        [_text(TARGETS[item['key']][0]), ('≥ ' if item['key'] == 'rps_min' else '≤ ') +
         _numeric(item['target']) + ' ' + _text(TARGETS[item['key']][1]),
         _numeric(item['actual']), STATUS_LABELS.get(item['status'], '数据不足')]
        for item in acceptance['checks'] if item.get('key') in TARGETS
    ]))
    parts.append('<p class="muted">验收使用本次运行保存的标准；执行完成不等于性能达标。未填写的指标不参与验收。</p></section>')
    overall = [('请求数', 'requests', '次'), ('失败请求数', 'failures', '次'), ('错误率', 'error_rate_percent', '%'),
               ('平均请求吞吐量', 'rps', '次/秒'), ('峰值区间吞吐量', 'peak_interval_rps', '次/秒'),
               ('平均响应时间', 'avg_response_time', 'ms'), ('P95 响应时间', 'p95', 'ms'),
               ('P99 响应时间', 'p99', 'ms'), ('实际统计时长', 'elapsed_seconds', '秒')]
    parts.append('<section><h2>总体指标</h2><div class="metrics">' + ''.join(
        f'<article><span>{label}</span><strong>{_numeric(evidence.get("overall." + key))}</strong><small>{unit}</small></article>'
        for label, key, unit in overall) + '</div></section>')
    trend = _list(evidence.get('trend.overall'))
    parts.append('<section><h2>采样趋势</h2>' +
                 _chart('区间请求吞吐量', _list(evidence.get('trend.throughput')), 'sample_index', 'rps', '次/秒', '原采样序号') +
                 _chart('累计 P95 响应时间', trend, 'elapsed_seconds', 'p95', 'ms', '秒') +
                 _chart('当前并发用户数', trend, 'elapsed_seconds', 'users', '人', '秒') +
                 '<p class="muted">吞吐量曲线表示各保留采样区间的平均值，非瞬时 QPS；P95 为截至采样时的累计统计。</p></section>')
    endpoints = [(key, _dict(value)) for key, value in evidence.items() if key.startswith('endpoint.')]
    parts.append('<section><h2>接口统计</h2>' + _table(
        ['接口编号', '方法'] + [label for _, label in METRIC_COLUMNS],
        [[_text('接口 ' + key.split('.')[1]), _text(value.get('method'))] +
         [_numeric(value.get(metric)) for metric, _ in METRIC_COLUMNS] for key, value in endpoints]
    ) + '<p class="muted">接口编号按本次采集的统计顺序排列；报告不包含请求地址或请求内容。</p></section>')
    nodes = [(key, _dict(value)) for key, value in evidence.items() if key.startswith('node.')]
    parts.append('<section><h2>节点统计</h2>' + _table(
        ['节点编号', '状态', '分配用户', '请求数', '失败数', 'P95（ms）', 'Worker 峰值 CPU（%）', 'Worker 峰值内存（MiB）'],
        [[_text('节点 ' + key.split('.')[1]), STATUS_LABELS.get(value.get('status'), '未知'),
          *[_numeric(value.get(metric)) for metric in ('assigned_users', 'requests', 'failures', 'p95',
                                                       'peak_worker_cpu_percent', 'peak_worker_memory_mib')]]
         for key, value in nodes]
    ) + '</section>')
    parts.append('<section><h2>失败样本摘要</h2>' + _table(['失败类别', '去重样本数'], [
        [_text(ERROR_LABELS.get(key, '其他错误')), _numeric(count)]
        for key, count in _dict(evidence.get('errors.samples')).items()
    ]) + '<p class="muted">样本经过去重与截断，样本数不是失败请求次数。请求内容、响应内容和原始异常信息不包含在报告中。</p></section>')
    diagnosis = build_failure_diagnosis(run)
    if diagnosis['items']:
        parts.append('<section><h2>失败定位与排查建议</h2>' + _table(
            ['样本定位', '去重样本数', '排查建议'],
            [[f'<div class="detail-cell">{_text(item.get("label"), 300)}</div>', _numeric(item.get('sample_count')),
              f'<div class="detail-cell">{_text(item.get("suggestion"), 800)}</div>'] for item in diagnosis['items']]
        ) + '<ul>' + ''.join(f'<li>{_text(item, 1000)}</li>' for item in diagnosis['limitations']) + '</ul></section>')
    analysis = run.analyses.filter(status='completed', analysis_type='load_summary').order_by('-created_at', '-id').first()
    parts.append('<section><h2>已有 AI 分析</h2>')
    if analysis:
        result = _dict(analysis.result)
        parts.append(f'<p class="muted">分析时间：{_date(analysis.finished_at or analysis.created_at)}。以下为已保存的辅助建议，不替代性能验收。</p>')
        if analysis.targets != targets:
            parts.append('<p class="muted">这份历史 AI 分析使用的目标与本次运行验收标准不同，请以“性能验收”区域为准。</p>')
        parts.append(f'<p class="prose">{_text(result.get("summary"), 1200)}</p>')
        for item in _list(result.get('findings'))[:12]:
            item = _dict(item)
            kind = '待验证推测' if item.get('kind') == 'hypothesis' else '数据观察'
            parts.append(f'<article class="finding"><h3>{_text(item.get("title"), 120)} <small>{kind}</small></h3>'
                         f'<p class="prose">{_text(item.get("detail"), 1600)}</p>'
                         f'<p class="prose">建议：{_text(item.get("recommendation"), 1200)}</p></article>')
        limits = [value for value in _list(result.get('limitations'))[:30] if isinstance(value, str)]
        if limits:
            parts.append('<h3>分析局限</h3><ul>' + ''.join(f'<li>{_text(value, 1000)}</li>' for value in limits) + '</ul>')
    else:
        parts.append('<p class="muted">尚无已完成的 AI 分析。导出报告不会发起新的分析。</p>')
    parts.append('</section><section><h2>数据口径与局限</h2><ul>' +
                 ''.join(f'<li>{_text(value, 1000)}</li>' for value in safe['limitations']) + '</ul></section>')
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>性能测试报告</title><style>
*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#172033;font:15px/1.7 system-ui,-apple-system,sans-serif}
main{max-width:1180px;margin:32px auto;padding:0 24px}header,section{background:white;padding:26px 30px;margin:0 0 20px;border:1px solid #e2e8f0;border-radius:14px}
h1{font-size:30px;margin:6px 0 16px;overflow-wrap:anywhere}h2{font-size:21px;margin:0 0 18px}h3{font-size:16px;margin:0 0 12px}p{margin:8px 0}.eyebrow{color:#2563eb;font-weight:600}.muted,small{color:#64748b}.verdict{font-size:22px;font-weight:700;color:#1e40af}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metrics article{border:1px solid #e2e8f0;border-radius:8px;padding:18px}.metrics span{display:block;color:#64748b}.metrics strong{display:inline-block;font-size:27px;margin:6px 10px 0 0}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{text-align:left;border-bottom:1px solid #e2e8f0;padding:10px 12px;white-space:nowrap}th{background:#f8fafc;font-weight:600}.detail-cell{min-width:180px;max-width:560px;white-space:normal;overflow-wrap:anywhere}.chart{padding:16px;border:1px solid #e2e8f0;margin:14px 0;border-radius:8px}.chart svg{width:100%;max-height:310px;display:block}.chart text{font-size:12px;fill:#64748b}.finding{border-left:3px solid #93c5fd;padding:12px 16px;margin:16px 0;background:#f8fafc}.prose,li{white-space:pre-wrap;overflow-wrap:anywhere}li{margin:7px 0}
@media(max-width:650px){main{padding:0 10px;margin:12px auto}header,section{padding:20px 16px}.metrics{grid-template-columns:repeat(2,1fr)}h1{font-size:24px}}
@media print{body{background:white}main{max-width:none;margin:0;padding:0}header,section{border:0;padding:10px 0}.chart,.finding,.metrics article{break-inside:avoid}.table-wrap{overflow:visible}th,td{white-space:normal;font-size:11px;padding:5px}}
</style></head><body><main>''' + ''.join(parts) + '</main></body></html>'
