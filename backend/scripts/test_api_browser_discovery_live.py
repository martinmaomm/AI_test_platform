"""Explicit opt-in acceptance against a previously authorized test website.

Reads one owner's existing model/MCP and one prior UI description; copies them
only into a temporary SQLite database. It never migrates or writes the platform
database. The target website WILL be operated and the selected provider billed.
No prompts, passwords, keys or response bodies are printed in the summary.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from test_api_workspace_browser import bootstrap, BACKEND


def private_evidence(output, name, value):
    """Retain a failed candidate for diagnosis without printing its credentials."""
    path = output / name
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
    return str(path)


def existing_config(model_id, generation_id):
    # Read only. stdout is a private subprocess pipe consumed in this process,
    # not forwarded to a terminal, logfile or acceptance artifact.
    code = """
import json, os, sys
os.environ['ANONYMIZED_TELEMETRY']='false'
os.environ['MCP_USE_ANONYMIZED_TELEMETRY']='false'
sys.path[:0]=['.', 'apps']
os.environ['DJANGO_SETTINGS_MODULE']='aits_backend.settings'
import django; django.setup()
from ai_core.models import LLMConfiguration, MCPConfiguration
from web_testing.models import WebUIScriptGeneration
from web_testing.generation_preflight import resolve_active_playwright_mcp_config
model=LLMConfiguration.objects.get(id=int(sys.argv[1]),is_active=True,model_type='llm')
generation=WebUIScriptGeneration.objects.get(id=sys.argv[2],user_id=model.created_by_id)
selected=resolve_active_playwright_mcp_config(model.created_by_id)
assert selected, 'No current owner-scoped Playwright MCP'
print('AITS_PRIVATE_CONFIG='+json.dumps({'model':{key:getattr(model,key) for key in ['provider','provider_name','model_name','api_key','base_url','extra_config']},'mcp':selected[1], 'description':generation.description_safe,'target_url':generation.target_url}))
"""
    child = subprocess.run([sys.executable, '-c', code, str(model_id), str(generation_id)], cwd=BACKEND,
                           capture_output=True, text=True, timeout=30)
    if child.returncode != 0:
        raise RuntimeError('读取现有模型/探索配置失败；未启动真实验收。')
    row = next((line for line in child.stdout.splitlines() if line.startswith('AITS_PRIVATE_CONFIG=')), None)
    if not row:
        raise RuntimeError('配置读取协议无效。')
    return json.loads(row.split('=', 1)[1])


@contextmanager
def network_scope(allowed_origins):
    """Reject requests-runtime calls outside the explicitly authorized API origin.

MCP website requests are scoped by the new browser task itself; this wrapper is
for the direct replay within this acceptance process, not a platform policy.
"""
    import requests
    from urllib.parse import urlsplit
    original = requests.Session.request
    def guarded(session, method, url, *args, **kwargs):
        parsed = urlsplit(url)
        if f'{parsed.scheme}://{parsed.netloc}' not in allowed_origins:
            raise RuntimeError('Acceptance replay blocked an unapproved origin')
        return original(session, method, url, *args, **kwargs)
    with patch.object(requests.Session, 'request', guarded):
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', required=True)
    parser.add_argument('--model-id', type=int, required=True)
    parser.add_argument('--generation-id', required=True)
    parser.add_argument('--api-origin', required=True)
    parser.add_argument('--reuse-trace', type=Path, help='Reuse an existing private real-browser network.jsonl; do not operate the UI again.')
    args = parser.parse_args()
    config = existing_config(args.model_id, args.generation_id)
    os.environ['ANONYMIZED_TELEMETRY'] = 'false'
    os.environ['MCP_USE_ANONYMIZED_TELEMETRY'] = 'false'
    output = BACKEND / 'logs' / 'api-browser-discovery-live'
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {'scope': 'live MCP and model; disposable platform database', 'completed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='aits-discovery-live-') as temp:
            fixture = bootstrap(Path(temp))
            # Explicit --run-live opts this isolated platform instance into
            # real target requests, including its requests worker subprocess.
            os.environ.pop('AITS_OFFLINE_TEST_NETWORK', None)
            from django.conf import settings
            settings.API_BROWSER_DISCOVERY_ENABLED = True
            from django.contrib.auth import get_user_model
            from django.db import transaction
            from ai_core.models import LLMConfiguration, MCPConfiguration
            from api_testing.models import BrowserDiscoveryTask, APIWorkspace
            from api_testing.browser_discovery import discovery_limits, handoff_to_workspace, normalize_http_url, task_trace_file
            from api_testing.tasks import run_browser_discovery_async
            from api_testing.tasks import _finish_browser_discovery
            from api_testing.workspace_service import endpoint_specs
            from api_testing.workspace_views import _queue_pipeline
            from api_testing.workspace_tasks import generate_and_verify_api_workspace
            from api_testing.requests_runtime import run_case, export_python

            owner = get_user_model().objects.get(pk=fixture['user_id'])
            model = LLMConfiguration.objects.create(created_by=owner, **config['model'])
            MCPConfiguration.objects.create(created_by=owner, name='playwright', raw_config=json.dumps(config['mcp']))
            api_origin = normalize_http_url(args.api_origin, label='API origin', origin_only=True)
            task = BrowserDiscoveryTask.objects.create(
                owner=owner, project_id=fixture['project_id'], model_id=model.id,
                target_url=config['target_url'], api_origin=api_origin,
                description=config['description'], allow_test_data_writes=True,
                exploration_timeout_seconds=900, limits=discovery_limits(), task_id=str(__import__('uuid').uuid4()),
            )
            report.update(task_id=task.task_id, target_url=task.target_url, api_origin=api_origin,
                          model_name=model.model_name, provider_name=model.provider_name)
            private_output = output / task.task_id
            private_output.mkdir(mode=0o700, exist_ok=True)
            print(json.dumps({'event': 'started', 'task_id': task.task_id, 'target_url': task.target_url}, ensure_ascii=False), flush=True)
            if args.reuse_trace:
                source = args.reuse_trace.resolve(strict=True)
                capture_root = (BACKEND / 'logs' / 'api-browser-discovery').resolve()
                if source.name != 'network.jsonl' or not source.is_relative_to(capture_root):
                    raise RuntimeError('只能复用本项目私有采集目录内的 network.jsonl。')
                with source.open(encoding='utf-8') as stream:
                    start = json.loads(stream.readline())
                if start.get('event') != 'capture_started' or api_origin not in start.get('allowed_origins', []):
                    raise RuntimeError('已保存证据的授权 API origin 与本次验收不一致。')
                destination = task_trace_file(task)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
                task.status = 'running'
                task.save(update_fields=['status'])
                _finish_browser_discovery(str(task.id), task.version, task.task_id, {
                    'completed': True, 'summary': '复用已完成的真实 MCP 采集证据；没有重新操作页面。',
                })
                report.update(capture_reused=True, original_trace_file=str(source))
            else:
                run_browser_discovery_async.run(str(task.id), task.version, task.task_id)
            task.refresh_from_db()
            report.update(capture_status=task.status, error_code=task.error_code,
                          tool_calls=task.tool_calls, model_calls=task.model_calls, trace_file=str(task_trace_file(task)))
            records = list(task.records.filter(is_eligible=True).order_by('sequence'))
            report['records'] = len(records)
            if task.status not in {'completed', 'partial'} or not records:
                raise RuntimeError('真实探索未取得可交接接口，请查阅私有采集证据。')
            with transaction.atomic():
                handoff, _ = handoff_to_workspace(task=task, owner=owner, version=task.version, record_ids=[record.id for record in records])
            workspace = APIWorkspace.objects.get(pk=handoff.workspace_id)
            endpoints = endpoint_specs(workspace.project_id, workspace.endpoint_ids, spec_id=workspace.spec_id)
            report['endpoints'] = [{'method': item['method'], 'path': item['path']} for item in endpoints]
            print(json.dumps({'event': 'captured', 'records': len(records), 'endpoints': len(endpoints), 'tool_calls': task.tool_calls}), flush=True)
            with patch.object(generate_and_verify_api_workspace, 'apply_async'), transaction.atomic():
                _queue_pipeline(workspace, revision=workspace.revision, mode='generate', target_url=api_origin,
                                variables={}, endpoints=endpoints)
            generate_and_verify_api_workspace.run(workspace.id, workspace.revision, workspace.task_id)
            workspace.refresh_from_db()
            report['generation_status'] = workspace.generation.get('status')
            report['rounds'] = [{'attempt': row.get('attempt'), 'status': row.get('status')}
                                for row in workspace.generation.get('rounds', [])]
            if workspace.generation.get('status') != 'passed':
                report['failure_evidence'] = private_evidence(private_output, 'generation.json', {
                    'generation': workspace.generation, 'candidate': workspace.candidate,
                })
                raise RuntimeError('真实模型生成的候选尚未全部验证通过。')
            candidate = workspace.candidate['draft']
            report['candidate_evidence'] = private_evidence(private_output, 'verified-candidate.json', candidate)
            replays = []
            for index in range(2):
                with network_scope({api_origin}):
                    result = run_case(f'live-independent-{index}', candidate, options={'allowed_origin': api_origin})
                replays.append({'success': result.get('success'), 'steps': len(result.get('step_datas') or [])})
                report['fresh_session_replays'] = replays
                if not result.get('success'):
                    report['failure_evidence'] = private_evidence(private_output, 'replay.json', result)
                    raise RuntimeError('候选独立重跑未通过；不会删除或放宽断言。')
            file = Path(temp) / 'test_discovered_api.py'
            file.write_text(export_python(candidate), encoding='utf-8')
            exported = subprocess.run([sys.executable, str(file)], capture_output=True, text=True, timeout=120)
            report['python_export'] = 'passed' if exported.returncode == 0 else 'failed'
            if exported.returncode:
                report['failure_evidence'] = private_evidence(private_output, 'export.json', {
                    'stdout': exported.stdout, 'stderr': exported.stderr, 'returncode': exported.returncode,
                })
                raise RuntimeError('独立 Python 导出执行未通过。')
            report['completed'] = True
    finally:
        # Only non-secret acceptance metrics; do not persist model config or
        # candidate code here because it may include test login credentials.
        (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
