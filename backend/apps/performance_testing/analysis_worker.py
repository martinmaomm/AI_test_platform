"""Private JSON-lines model subprocess; stdout carries only the bounded protocol."""
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time


def _start_watchdog(deadline, parent_pid):
    """Bound silent socket waits and stop an orphan even after parent SIGKILL."""
    def watch():
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or os.getppid() != parent_pid:
                os._exit(1)  # This private child owns no persisted task state.
            time.sleep(min(0.2, remaining))
    threading.Thread(target=watch, name='analysis-deadline', daemon=True).start()


def main():
    started = time.monotonic()
    protocol = sys.stdout
    sys.stdout = sys.stderr
    logging.disable(logging.CRITICAL)

    def send(message):
        protocol.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + '\n')
        protocol.flush()

    try:
        raw = sys.stdin.buffer.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError('input limit')
        request = json.loads(raw)
        budget = float(request['budget_seconds'])
        _start_watchdog(started + budget, int(request['parent_pid']))
        backend = Path(__file__).resolve().parents[2]
        sys.path[:0] = [str(backend), str(backend / 'apps')]
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()
        logging.disable(logging.CRITICAL)
        from performance_testing.analysis_engine import _stream_analysis_text
        from performance_testing.analysis_process import AnalysisTransportError

        def remaining():
            return budget - (time.monotonic() - started)

        def check_active():
            if remaining() <= 0:
                raise AnalysisTransportError('analysis_timeout')

        check_active()
        result = _stream_analysis_text(
            request['model_config_id'], request['payload'], check_active, remaining,
            on_event=lambda event: send({'type': 'event', 'event': event}),
        )
        check_active()
        send({'type': 'result', 'text': result})
    except Exception as exc:
        code = getattr(exc, 'code', None)
        if exc.__class__.__name__ in ('KnowledgeLLMTimeout', 'SoftTimeLimitExceeded'):
            code = 'analysis_timeout'
        if code not in ('analysis_timeout', 'INVALID_MODEL_OUTPUT'):
            code = 'model_error'
        send({'type': 'error', 'code': code})


if __name__ == '__main__':
    main()
