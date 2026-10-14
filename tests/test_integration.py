"""Opt in with DEVSERVERS_INTEGRATION=1. Only manages processes created by this test."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import unittest

import devservers as app
from urllib.request import urlopen

SCRIPT = Path(__file__).resolve().parents[1] / 'devservers.py'
SERVER = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer
import os
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write((os.environ['ROUNDTRIP_MARKER'] + '|' + os.getcwd()).encode())
    def log_message(self, *args): pass
class LocalServer(HTTPServer):
    def server_bind(self):
        # HTTPServer normally performs a reverse DNS lookup, which this test does not need.
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.socket.getsockname()[1]
server = LocalServer(('127.0.0.1', int(os.environ.get('ROUNDTRIP_PORT', '0'))), Handler)
os.environ['ROUNDTRIP_PORT'] = str(server.server_port)
print(server.server_port, flush=True)
server.serve_forever()
'''


@unittest.skipUnless(os.environ.get('DEVSERVERS_INTEGRATION') == '1', 'opt-in macOS process test')
class RoundTripTests(unittest.TestCase):
    def test_discover_stop_restart_preserves_environment_and_directory(self):
        with tempfile.TemporaryDirectory(prefix='devservers-test-') as directory:
            project = Path(directory).resolve() / 'project with spaces'
            project.mkdir()
            (project / '.git').mkdir()
            env = dict(os.environ, DEVSERVERS_HOME=str(Path(directory) / 'state'),
                       DEVSERVERS_QUIET='1', ROUNDTRIP_MARKER='roundtrip-ok')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                env['ROUNDTRIP_PORT'] = str(reservation.getsockname()[1])
            proc = subprocess.Popen([sys.executable, '-u', '-c', SERVER], cwd=project,
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True, text=True)
            restarted = None
            try:
                if not select.select([proc.stdout], [], [], 30)[0]:
                    proc.terminate()
                    _, errors = proc.communicate(timeout=5)
                    self.fail('server startup timed out: ' + errors)
                line = proc.stdout.readline()
                if not line:
                    _, errors = proc.communicate(timeout=5)
                    self.fail('server exited before startup: ' + errors)
                port = int(line)
                def cli(*args):
                    result = subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                                            capture_output=True, text=True, timeout=40)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    return json.loads(result.stdout)
                state = cli('json')
                server = next((s for s in state['servers'] if s['root'] == proc.pid), None)
                if server is None:
                    snapshot = app.Snapshot()
                    self.fail('Fixture not discovered: ' + json.dumps({
                        'python': sys.executable, 'exe': snapshot.exe(proc.pid),
                        'system': snapshot.is_system(proc.pid),
                        'root': snapshot.root_of(proc.pid), 'pid': proc.pid,
                        'ports': sorted(snapshot.ports.get(proc.pid, [])),
                    }))
                self.assertIn(port, server['ports'])
                cli('stop', str(proc.pid), server['started'])
                proc.wait(timeout=5)
                state = cli('json')
                stopped = state['stopped'][0]
                self.assertEqual(stopped['cwd'], str(project))
                cli('start', stopped['id'])
                state = cli('json')
                restarted = next(s for s in state['servers'] if s['cwd'] == str(project))
                self.assertIn(port, restarted['ports'])
                with urlopen(f'http://127.0.0.1:{port}', timeout=5) as response:
                    self.assertEqual(response.read().decode(), 'roundtrip-ok|' + str(project))
                self.assertEqual(state['stopped'], [])
                cli('stop', str(restarted['root']), restarted['started'])
                restarted = None
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=5)
                proc.stdout.close()
                proc.stderr.close()
                if restarted is not None:
                    # This PID was returned after restarting our own fixture.
                    try:
                        os.kill(restarted['root'], signal.SIGTERM)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()
