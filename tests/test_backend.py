"""CLI tests: real adapters, synthetic local files, isolated executable shims."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import unittest

BACKEND = Path(__file__).resolve().parents[1] / 'runtime/0.1.0/backend.py'

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        self.env = {'HOME':str(self.home), 'PATH':str(self.bin), 'LANG':'C.UTF-8',
                    'TMPDIR':str(self.home), 'PYTHONDONTWRITEBYTECODE':'1'}

    def put(self, rel, text):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p

    def shim(self, name, rc=0, body=''):
        p = self.bin / name
        p.write_text('#!/usr/bin/python3\nimport os,sys,json,time\nfrom pathlib import Path\n'
                     'p=Path(os.environ["HOME"])/"calls.jsonl"\n'
                     'with p.open("a") as f: f.write(json.dumps({"argv":sys.argv,"stdin":sys.stdin.read()})+"\\n")\n'
                     +body+'\nsys.exit('+str(rc)+')\n')
        p.chmod(0o755)

    def calls(self):
        p = self.home / 'calls.jsonl'
        return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []

    def cli(self, *args, rc=0):
        r = subprocess.run(['/usr/bin/python3',str(BACKEND),*args],cwd='/',env=self.env,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        self.assertEqual(r.returncode,rc,r.stderr.decode()+r.stdout.decode())
        self.assertTrue(r.stdout.strip(), 'backend must emit JSON')
        self.assertLessEqual(len(r.stdout),1048576)
        self.assertNotIn(b'Traceback',r.stdout+r.stderr)
        return json.loads(r.stdout)

    def test_offline_box_is_private(self):
        self.assertEqual(os.environ['HOME'],'/home/test')
        for key in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','HYPRLAND_INSTANCE_SIGNATURE','PYTHONPATH'):
            self.assertNotIn(key,os.environ)
        self.assertEqual(sorted(p.name for p in Path('/home').iterdir()),['test'])
        self.assertFalse(Path('/srv').exists())
        self.assertFalse(Path('/run/user').exists())
        interfaces = Path('/proc/net/dev').read_text().splitlines()[2:]
        self.assertTrue(all(s.strip().startswith('lo:') for s in interfaces))
        with socket.socket() as sock:
            sock.settimeout(.2)
            self.assertNotEqual(sock.connect_ex(('192.0.2.1',443)),0)

    def test_catalog_contract(self):
        result = self.cli('catalog')
        self.assertIsInstance(result['rows'],list)
        self.assertIsInstance(result['message'],str)
        self.assertIn('actions',result)
        self.assertIn('actionLabel',result)
        seen = set()
        for row in result['rows']:
            self.assertEqual(set(row)-{'id','label','detail','preview','color'},set())
            for key in ('id','label','detail'):
                self.assertIsInstance(row[key],str)
                self.assertTrue(row[key])
                self.assertLessEqual(len(row[key]),512)
            self.assertNotIn(row['id'],seen)
            seen.add(row['id'])
            if row.get('preview'):
                self.assertTrue(Path(row['preview']).is_absolute())
        self.assertEqual(self.calls(),[])

    def desktop(self,name='sample.desktop',extra='',base=None):
        root=base or self.home/'.local/share'
        p=root/'applications'/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('[Desktop Entry]\nType=Application\nName=Sample Editor\nComment=Edit local documents\nExec=do-not-evaluate %F\n'+extra)
        self.env['XDG_DATA_DIRS']=str(self.home/'system')
        return p

    def test_installed_desktop_catalog_includes_real_details(self):
        self.desktop(extra='Categories=Office;Utility;\n')
        r=self.cli('catalog')
        self.assertEqual(len(r['rows']),1)
        row=r['rows'][0]
        self.assertEqual(row['id'],'sample.desktop')
        self.assertEqual(row['label'],'Sample Editor')
        self.assertIn('Edit local documents',row['detail'])
        self.assertIn('sample.desktop',row['detail'])
        self.assertIn('Office',row['detail'])
        self.assertEqual(self.calls(),[])

    def test_hidden_user_entry_masks_system_entry(self):
        self.desktop(extra='Hidden=true\n')
        self.desktop(base=self.home/'system')
        self.assertEqual(self.cli('catalog')['rows'],[])

    def test_empty_or_relative_data_home_keeps_hidden_override(self):
        self.desktop(extra='Hidden=true\n')
        self.desktop(base=self.home/'system')
        self.shim('gio')
        for value in ('', 'relative'):
            with self.subTest(value=value):
                self.env['XDG_DATA_HOME']=value
                self.assertEqual(self.cli('catalog')['rows'],[])
                result=self.cli('action','--id','sample.desktop','--verb','launch','--apply',rc=1)
                self.assertFalse(result['ok'])
        self.assertEqual(self.calls(),[])

    def test_empty_data_dirs_uses_specification_defaults(self):
        import importlib.util
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('app_deck_roots',BACKEND)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict(os.environ,{'HOME':str(self.home),'XDG_DATA_DIRS':''},clear=True):
            self.assertEqual(module.data_roots(),[self.home/'.local/share',Path('/usr/local/share'),Path('/usr/share')])

    def test_visibility_filters_and_tryexec(self):
        self.env['XDG_CURRENT_DESKTOP']='Hyprland'
        for name,extra in [('hidden','Hidden=true\n'),('nodisplay','NoDisplay=true\n'),('only','OnlyShowIn=GNOME;\n'),('not','NotShowIn=Hyprland;\n'),('missing','TryExec=not-installed\n')]:
            self.desktop(name+'.desktop',extra)
        self.desktop('visible.desktop','OnlyShowIn=Hyprland;GNOME;\nTryExec=installed\n')
        self.shim('installed')
        rows=self.cli('catalog')['rows']
        self.assertEqual([r['id'] for r in rows],['visible.desktop'])
        self.assertEqual(self.calls(),[])

    def test_malformed_oversized_and_nonregular_entries_are_skipped(self):
        p=self.desktop()
        p.write_text('[Desktop Entry]\nType=Application\nName=Duplicate\nName=Again\n')
        self.desktop('huge.desktop','Comment='+'x'*70000)
        self.desktop('control.desktop','Name=Bad\x01name\n')
        self.desktop('link.desktop','Type=Link\nURL=https://example.test\n')
        p=self.desktop('fifo.desktop'); p.unlink(); os.mkfifo(p)
        self.assertEqual(self.cli('catalog')['rows'],[])

    def test_nested_desktop_id_and_bounded_plain_metadata(self):
        self.desktop('vendor/nested.desktop','GenericName=Text Tool\n')
        rows=self.cli('catalog')['rows']
        self.assertEqual([r['id'] for r in rows],['vendor-nested.desktop'])
        self.assertIn('Text Tool',rows[0]['detail'])

    def test_launch_delegates_only_known_path_without_parsing_exec(self):
        path=self.desktop()
        path.write_text('[Desktop Entry]\nType=Application\nName=Literal $(name)\nExec=sh -c "touch '+str(self.home/'PWNED')+'"\n')
        self.shim('gio')
        result=self.cli('action','--id','sample.desktop','--verb','launch','--apply')
        self.assertTrue(result.get('ok',False))
        self.assertEqual(self.calls()[0]['argv'][1:],['launch',str(path)])
        self.assertFalse((self.home/'PWNED').exists())
        self.assertIn('accepted',result['message'].lower())
        self.assertIn('not confirmed',result['message'].lower())

    def test_launch_revalidates_visibility_and_vanished_entry(self):
        path=self.desktop()
        self.shim('gio')
        self.cli('catalog')
        path.unlink()
        self.assertFalse(self.cli('action','--id','sample.desktop','--verb','launch','--apply',rc=1)['ok'])
        self.desktop(extra='Hidden=true\n')
        self.assertFalse(self.cli('action','--id','sample.desktop','--verb','launch','--apply',rc=1)['ok'])
        self.assertEqual(self.calls(),[])

    def test_launch_requires_apply_and_whitelisted_membership(self):
        self.desktop()
        self.shim('gio')
        for args in [('action','--id','sample.desktop','--verb','launch'),('action','--id','../../bad.desktop','--verb','launch','--apply'),('action','--id','sample.desktop','--verb','restore','--apply'),('catalog','--apply'),('garbage',),('action','--id','sample.desktop','--verb','launch','--apply','--unknown')]:
            with self.subTest(args=args):
                self.assertFalse(self.cli(*args,rc=1)['ok'])
        self.assertEqual(self.calls(),[])

    def test_launch_helper_absence_failure_and_timeout_are_not_success(self):
        self.desktop()
        args=('action','--id','sample.desktop','--verb','launch','--apply')
        self.assertFalse(self.cli(*args,rc=1)['ok'])
        self.shim('gio',rc=7,body='sys.stderr.write("private-provider-info"*100000)')
        result=self.cli(*args,rc=1)
        self.assertFalse(result['ok'])
        self.assertNotIn('private-provider-info',result['message'])
        self.shim('gio',body='time.sleep(30)')
        self.assertIn('timed out',self.cli(*args,rc=1)['message'])

    def test_local_named_and_absolute_raster_icons_only(self):
        import base64
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2kAAAAASUVORK5CYII=')
        icon=self.home/'.local/share/icons/hicolor/48x48/apps/sample.png'
        icon.parent.mkdir(parents=True);icon.write_bytes(png)
        path=self.desktop(extra='Icon=sample\n')
        row=self.cli('catalog')['rows'][0]
        self.assertEqual(row.get('preview'),str(icon))
        for value in ['https://example.test/icon.png','../../escape','missing',str(self.put('not-image.png','not an image'))]:
            with self.subTest(icon=value):
                path.write_text('[Desktop Entry]\nType=Application\nName=Sample\nExec=never-run\nIcon='+value+'\n')
                self.assertNotIn('preview',self.cli('catalog')['rows'][0])
        path.write_text('[Desktop Entry]\nType=Application\nName=Sample\nExec=never-run\nIcon='+str(icon)+'\n')
        self.assertEqual(self.cli('catalog')['rows'][0]['preview'],str(icon))

    def test_catalog_display_limit_keeps_fresh_full_launch_membership(self):
        for n in range(1030):
            self.desktop(f'app{n:04}.desktop')
        result=self.cli('catalog')
        self.assertLessEqual(len(result['rows']),1024)
        self.assertIn('limited',result['message'].lower())
        self.shim('gio')
        self.assertTrue(self.cli('action','--id','app1029.desktop','--verb','launch','--apply')['ok'])
        self.assertTrue(self.calls()[0]['argv'][-1].endswith('/app1029.desktop'))

    def test_independent_deadline_survives_backend_termination(self):
        import time
        self.desktop()
        self.shim('gio',body='import signal\nchild=os.fork()\nif child == 0:\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n time.sleep(30)\n sys.exit(0)\n(Path(os.environ["HOME"])/"child.pid").write_text(str(child))\ntime.sleep(30)')
        proc=subprocess.Popen(['/usr/bin/python3',str(BACKEND),'action','--id','sample.desktop','--verb','launch','--apply'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        pidfile=self.home/'child.pid'
        deadline=time.monotonic()+2
        while not pidfile.exists() and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertTrue(pidfile.exists())
        pid=int(pidfile.read_text())
        proc.terminate();proc.wait(timeout=2)
        deadline=time.monotonic()+5
        def running():
            statfile=Path('/proc')/str(pid)/'stat'
            return statfile.exists() and statfile.read_text().split()[2] not in ('Z','X')
        while running() and time.monotonic()<deadline:
            time.sleep(.05)
        alive=running()
        if alive:
            os.kill(pid,9)
        self.assertFalse(alive,'helper outlived its deadline after backend termination')

    def test_utf8_output_budget_precedes_row_limit(self):
        for n in range(450):
            path=self.desktop(f'unicode{n}.desktop')
            path.write_text('[Desktop Entry]\nType=Application\nExec=never-run\nName='+('界'*160)+'\nGenericName='+('界'*160)+'\nComment='+('界'*160)+'\nCategories='+('界'*160)+'\n')
        result=self.cli('catalog')
        self.assertLess(len(result['rows']),450)
        self.assertLess(len(json.dumps(result,ensure_ascii=False).encode('utf-8')),810000)
        self.assertIn('limited',result['message'].lower())

if __name__ == '__main__':
    unittest.main()
