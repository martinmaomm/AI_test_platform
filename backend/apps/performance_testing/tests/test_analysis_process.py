import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest import TestCase
from unittest.mock import patch

from performance_testing.analysis_process import AnalysisTransportError, stream_in_process
from performance_testing import analysis_worker


class AnalysisProcessTests(TestCase):
    """Real local subprocesses, no Django child bootstrap or network/model calls."""

    def run_child(self, script, budget=2, check_active=lambda: None, on_event=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.py'
            path.write_text(script)
            children = []
            original = subprocess.Popen

            def spawn(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                return child

            with patch('performance_testing.analysis_process._worker_command',
                       return_value=[sys.executable, '-u', str(path)]), patch(
                       'performance_testing.analysis_process.subprocess.Popen', side_effect=spawn):
                try:
                    return stream_in_process(1, {'statistics': 'safe'}, check_active, budget, on_event)
                finally:
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll(), 'Child must always be reaped')
                    with self.assertRaises(ProcessLookupError):
                        os.kill(children[0].pid, 0)

    def test_events_and_result_cross_process_boundary(self):
        events = []
        result = self.run_child('''import json, sys
request = json.load(sys.stdin)
assert request['model_config_id'] == 1
print(json.dumps({'type':'event','event':{'phase':'waiting_response','attempt':1}}), flush=True)
print(json.dumps({'type':'result','text':'正文'}), flush=True)
''', on_event=events.append)
        self.assertEqual(result, '正文')
        self.assertEqual(events, [{'phase': 'waiting_response', 'attempt': 1}])

    def test_blocked_stream_is_stopped_at_deadline(self):
        started = time.monotonic()
        with self.assertRaises(AnalysisTransportError) as caught:
            self.run_child('import sys,time; sys.stdin.read(); time.sleep(30)', budget=0.15)
        self.assertEqual(caught.exception.code, 'analysis_timeout')
        self.assertLess(time.monotonic() - started, 2)

    def test_child_that_never_reads_stdin_cannot_block_parent(self):
        with self.assertRaises(AnalysisTransportError) as caught:
            self.run_child('import time; time.sleep(30)', budget=0.15)
        self.assertEqual(caught.exception.code, 'analysis_timeout')

    def test_revoked_parent_check_cleans_up_child(self):
        started = time.monotonic()

        def check():
            if time.monotonic() - started > 0.1:
                raise PermissionError('revoked')

        with self.assertRaises(PermissionError):
            self.run_child('import time; time.sleep(30)', check_active=check)

    def test_malformed_or_oversized_or_empty_protocol_fails_safely(self):
        for script in ("print('invalid secret-provider-error')", "print('x' * (512*1024+1))", 'pass'):
            with self.subTest(script=script), self.assertRaises(AnalysisTransportError) as caught:
                self.run_child(script)
            self.assertEqual(str(caught.exception), 'model_error')

    def test_safe_child_error_and_output_limit(self):
        for message, code in (({'type': 'error', 'code': 'secret'}, 'model_error'),
                              ({'type': 'error', 'code': 'analysis_timeout'}, 'analysis_timeout'),
                              ({'type': 'result', 'text': 'x' * 24001}, 'INVALID_MODEL_OUTPUT')):
            with self.subTest(code=code), self.assertRaises(AnalysisTransportError) as caught:
                self.run_child('import json,sys;sys.stdin.read();print(' + repr(json.dumps(message)) + ')')
            self.assertEqual(caught.exception.code, code)

    def test_child_watchdog_exits_while_transport_is_blocked(self):
        script = ("import importlib.util,os,time;"
                  f"s=importlib.util.spec_from_file_location('worker', {analysis_worker.__file__!r});"
                  "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                  "m._start_watchdog(time.monotonic()+0.15,os.getppid());time.sleep(30)")
        with subprocess.Popen([sys.executable, '-c', script]) as child:
            self.assertEqual(child.wait(timeout=2), 1)

    def test_child_watchdog_exits_after_parent_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'exited'
            # Instrument only the private child's exit to observe orphan cleanup.
            child_script = f'''import importlib.util,os,time
s=importlib.util.spec_from_file_location('worker', {analysis_worker.__file__!r})
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
original_exit=os._exit
def exited(code):
    open({str(marker)!r}, 'w').write(str(code))
    original_exit(code)
m.os._exit=exited
m._start_watchdog(time.monotonic()+5,os.getppid())
print('ready', flush=True)
time.sleep(30)
'''
            parent_script = f'''import subprocess,sys,time
child=subprocess.Popen([sys.executable,'-u','-c',{child_script!r}],stdout=subprocess.PIPE,text=True)
assert child.stdout.readline().strip() == 'ready'
print(child.pid,flush=True)
time.sleep(30)
'''
            with subprocess.Popen([sys.executable, '-u', '-c', parent_script],
                                  stdout=subprocess.PIPE, text=True) as parent:
                orphan_pid = int(parent.stdout.readline().strip())
                try:
                    parent.kill()
                    parent.wait(timeout=2)
                    deadline = time.monotonic() + 2
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), 'Orphan must exit before its 5-second budget')
                    self.assertEqual(marker.read_text(), '1')
                finally:
                    if not marker.exists():
                        try:
                            os.kill(orphan_pid, 9)
                        except ProcessLookupError:
                            pass
