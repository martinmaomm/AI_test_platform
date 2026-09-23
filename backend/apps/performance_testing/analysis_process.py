"""Bound blocking model SDK work even when Celery uses its solo pool."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time


MAX_MESSAGE_BYTES = 512 * 1024
MAX_PROTOCOL_BYTES = 2 * 1024 * 1024


class AnalysisTransportError(RuntimeError):
    def __init__(self, code='model_error'):
        self.code = code
        super().__init__(code)


def _worker_command():
    return [sys.executable, '-u', str(Path(__file__).with_name('analysis_worker.py'))]


def _seconds(value):
    return float(value() if callable(value) else value)


def stream_in_process(model_config_id, payload, check_active, remaining_seconds, on_event=None):
    budget = _seconds(remaining_seconds)
    if budget <= 0:
        raise AnalysisTransportError('analysis_timeout')
    deadline = time.monotonic() + budget
    encoded = json.dumps({'model_config_id': model_config_id, 'payload': payload,
                          'budget_seconds': budget, 'parent_pid': os.getpid()},
                         ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise AnalysisTransportError()
    check_active()
    # No credentials or prompts in argv, files, stderr, or process logs.
    process = subprocess.Popen(_worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    selector = selectors.DefaultSelector()
    pending = memoryview(encoded)
    buffer = bytearray()
    received_bytes = 0
    try:
        for pipe in (process.stdin, process.stdout):
            os.set_blocking(pipe.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            check_active()
            remaining = min(deadline - time.monotonic(), _seconds(remaining_seconds))
            if remaining <= 0:
                raise AnalysisTransportError('analysis_timeout')
            for key, _ in selector.select(min(0.2, remaining)):
                if key.fileobj is process.stdin:
                    try:
                        written = os.write(process.stdin.fileno(), pending[:8192])
                    except BrokenPipeError as exc:
                        raise AnalysisTransportError() from exc
                    pending = pending[written:]
                    if not pending:
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise AnalysisTransportError()
                received_bytes += len(chunk)
                buffer.extend(chunk)
                if received_bytes > MAX_PROTOCOL_BYTES:
                    raise AnalysisTransportError()
                while b'\n' in buffer:
                    line, _, tail = buffer.partition(b'\n')
                    buffer = bytearray(tail)
                    if len(line) > MAX_MESSAGE_BYTES:
                        raise AnalysisTransportError()
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError) as exc:
                        raise AnalysisTransportError() from exc
                    if not isinstance(message, dict):
                        raise AnalysisTransportError()
                    kind = message.get('type')
                    if kind == 'event' and isinstance(message.get('event'), dict):
                        if on_event:
                            on_event(message['event'])  # Runtime independently allowlists fields.
                    elif kind == 'result':
                        text = message.get('text')
                        if not isinstance(text, str) or len(text) > 24000:
                            raise AnalysisTransportError('INVALID_MODEL_OUTPUT')
                        # Data received at the deadline must not become a success.
                        if min(deadline - time.monotonic(), _seconds(remaining_seconds)) <= 0:
                            raise AnalysisTransportError('analysis_timeout')
                        check_active()
                        return text
                    elif kind == 'error':
                        code = message.get('code')
                        raise AnalysisTransportError(code if code in {
                            'analysis_timeout', 'INVALID_MODEL_OUTPUT', 'model_error',
                        } else 'model_error')
                    else:
                        raise AnalysisTransportError()
                if len(buffer) > MAX_MESSAGE_BYTES:
                    raise AnalysisTransportError()
    finally:
        selector.close()
        for pipe in (process.stdin, process.stdout):
            pipe.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
