import contextlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import devservers as app


def entry(number=1):
    return dict(id=f'{number:08x}', name='example', group='project', kind='http',
                cwd='/tmp', argv=['python3', '-m', 'http.server'], env={'PORT': '8000'},
                ports=[8000], stopped_at=1)


def append_entry(directory, number):
    app.STATE_DIR = Path(directory)
    app.STOPPED_FILE = app.STATE_DIR / 'stopped.json'
    with app.state_lock():
        items = app.load_stopped()
        time.sleep(0.02)
        app.save_stopped(items + [entry(number)])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'state'
        paths = patch.multiple(app, STATE_DIR=self.directory,
                               STOPPED_FILE=self.directory / 'stopped.json',
                               LOG_DIR=self.directory / 'logs')
        paths.start()
        self.addCleanup(paths.stop)

    def test_private_permissions_from_creation(self):
        observed = []
        replace = os.replace
        def inspect(source, destination):
            observed.append(Path(source).stat().st_mode & 0o777)
            replace(source, destination)
        with app.state_lock(), patch.object(app.os, 'replace', side_effect=inspect):
            app.save_stopped([entry()])
        self.assertEqual(observed, [0o600])
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(app.STOPPED_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(app.load_stopped(), [entry()])

    def test_failed_write_preserves_history(self):
        with app.state_lock():
            app.save_stopped([entry()])
            with patch.object(app.os, 'replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    app.save_stopped([entry(2)])
        self.assertEqual(app.load_stopped(), [entry()])
        self.assertEqual(list(self.directory.glob('.stopped-*')), [])

    def test_corrupt_history_is_preserved(self):
        self.directory.mkdir()
        app.STOPPED_FILE.write_text('{broken')
        with self.assertRaises(app.BackendError):
            app.load_stopped()
        self.assertEqual(app.STOPPED_FILE.read_text(), '{broken')

    def test_invalid_entries_are_rejected(self):
        self.directory.mkdir()
        for item in [dict(entry(), id='../escape'), dict(entry(), argv=[]),
                     dict(entry(), ports=['8000']), dict(entry(), env={'X': 3}), None]:
            with self.subTest(item=item):
                app.STOPPED_FILE.write_text(json.dumps([item]))
                with self.assertRaises(app.BackendError):
                    app.load_stopped()

    def test_concurrent_writers_keep_all_entries(self):
        workers = [multiprocessing.get_context('fork').Process(
            target=append_entry, args=(str(self.directory), i)) for i in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual({e['id'] for e in app.load_stopped()}, {f'{i:08x}' for i in range(8)})

    def test_symlink_directory_is_rejected(self):
        target = Path(self.temp.name) / 'target'
        target.mkdir()
        self.directory.symlink_to(target)
        with self.assertRaises(app.BackendError):
            with app.state_lock():
                self.fail('accepted symbolic link')

    def test_busy_port_keeps_restart_entry(self):
        with app.state_lock():
            app.save_stopped([entry()])
            with patch.object(app, 'Snapshot', return_value=SimpleNamespace(ports={42: {8000}})), \
                    patch.object(app.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(app.BackendError, 'en uso'):
                    app.cmd_start(entry()['id'])
                spawn.assert_not_called()
        self.assertEqual(app.load_stopped(), [entry()])

    def test_forget_preserves_other_entries(self):
        with app.state_lock():
            app.save_stopped([entry(1), entry(2)])
            app.cmd_forget(entry(1)['id'])
        self.assertEqual(app.load_stopped(), [entry(2)])


class ProcessTests(unittest.TestCase):
    def server(self, pid=20, kind='http'):
        return dict(root=pid, started='Wed Oct 14 09:00:00 2026', tree=[pid], kind=kind,
                    age=200000, name='example', group='project', cwd='/tmp',
                    ports=[8000], cmd='python3 -m http.server')

    def test_postgres_failure_never_falls_back_to_signals(self):
        snap = Mock()
        snap.name.return_value = 'postgres'
        with patch.object(app, 'same_process', return_value=True), \
                patch.object(app, 'stop_postgres', side_effect=app.BackendError('pg_ctl failed')), \
                patch.object(app, 'kill_tree') as kill:
            with self.assertRaises(app.BackendError):
                app.do_stop(snap, self.server(kind='postgres'), record=False)
            kill.assert_not_called()

    def test_postgres_missing_pgdata_is_not_stopped(self):
        snap = Mock()
        snap.args.return_value = ('/opt/postgres', ['postgres'], {})
        with patch.object(app, 'run') as run:
            with self.assertRaises(app.BackendError):
                app.stop_postgres(snap, self.server())
            run.assert_not_called()

    def test_postgres_reads_pgdata_environment(self):
        snap = Mock()
        snap.args.return_value = ('/opt/bin/postgres', ['postgres'], {'PGDATA': '/data'})
        with patch.object(app, 'run') as run:
            app.stop_postgres(snap, self.server())
        self.assertEqual(run.call_args.args[0],
                         ['/opt/bin/pg_ctl', 'stop', '-D', '/data', '-m', 'fast', '-w', '-t', '15'])

    def test_changed_pid_never_receives_signal(self):
        snap = SimpleNamespace(procs={20: {'started': 'original'}})
        with patch.object(app, 'same_process', return_value=False), patch.object(app.os, 'kill') as kill:
            app.kill_tree(snap, [20], grace=0)
            kill.assert_not_called()

    def test_stale_ui_cannot_stop_reused_pid(self):
        with patch.object(app, 'collect', return_value=(Mock(), [self.server()], [])), \
                patch.object(app, 'do_stop') as stop:
            with self.assertRaises(app.BackendError):
                app.cmd_stop(20, expected='different start time')
            stop.assert_not_called()

    def test_clean_only_stops_confirmed_servers(self):
        snap = Mock()
        snap.name.side_effect = lambda pid: 'postgres' if pid == 21 else 'node'
        targets = [self.server(20), self.server(21, 'postgres'), self.server(22)]
        with patch.object(app, 'collect', return_value=(snap, targets, [self.server(23)])), \
                patch.object(app, 'do_stop') as stop, patch.object(app, 'notify'):
            app.cmd_clean(ask=False, expected=[[20, targets[0]['started']]])
            stop.assert_called_once_with(snap, targets[0])

    def test_save_failure_prevents_stop(self):
        with patch.object(app, 'same_process', return_value=True), \
                patch.object(app, 'restart_spec', return_value={'argv': ['server'], 'cwd': '/tmp', 'env': {}}), \
                patch.object(app, 'load_stopped', return_value=[]), \
                patch.object(app, 'save_stopped', side_effect=OSError('disk full')), \
                patch.object(app, 'kill_tree') as kill:
            with self.assertRaises(OSError):
                app.do_stop(Mock(), self.server())
            kill.assert_not_called()

    def snapshot(self, parent_exe, parent_argv):
        snap = app.Snapshot.__new__(app.Snapshot)
        snap.uid = os.getuid()
        snap.procs = {20: {'ppid': 10, 'uid': snap.uid, 'cmd': 'server'},
                      10: {'ppid': 1, 'uid': snap.uid, 'cmd': ' '.join(parent_argv)}}
        snap._args = {20: ('/opt/bin/node', ['node', 'server.js'], {}),
                      10: (parent_exe, parent_argv, {})}
        return snap

    def test_generic_runtime_parent_is_not_a_launcher(self):
        for exe in ['/opt/bin/node', '/opt/bin/python3']:
            with self.subTest(exe=exe):
                snap = self.snapshot(exe, [exe, 'agent.js'])
                self.assertEqual(snap.root_of(20), 20)

    def test_node_running_npm_is_a_launcher(self):
        snap = self.snapshot('/opt/bin/node', ['node', '/opt/npm/bin/npm-cli.js', 'run', 'dev'])
        self.assertEqual(snap.root_of(20), 10)

    def test_system_app_parent_is_not_a_launcher(self):
        snap = self.snapshot('/Applications/Editor.app/node', ['npm', 'run', 'dev'])
        self.assertEqual(snap.root_of(20), 20)

    def test_subprocess_timeout_is_reported(self):
        with patch.object(app.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 10)):
            with self.assertRaises(app.BackendError):
                app.run(['ps'])

    def test_lsof_no_matches_is_empty(self):
        with patch.object(app.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='')):
            self.assertEqual(app.run(['lsof']), '')

    def test_json_contains_identity_without_environment(self):
        output = io.StringIO()
        server = dict(self.server(), cpu=1, rss=1024)
        with patch.object(app, 'collect', return_value=(Mock(), [server], [])), \
                patch.object(app, 'load_stopped', return_value=[entry()]), contextlib.redirect_stdout(output):
            app.render_json()
        state = json.loads(output.getvalue())
        self.assertEqual(state['servers'][0]['started'], server['started'])
        self.assertNotIn('env', state['stopped'][0])
        self.assertEqual(state['cleanable'], 1)


if __name__ == '__main__':
    unittest.main()
