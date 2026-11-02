"""CLI tests: real adapters, synthetic local files, isolated executable shims."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / Path(json.loads((ROOT / 'manifest.json').read_text())['entryPoints']['overlay']).parent / 'backend.py'

class BackendTests(unittest.TestCase):
    def test_400_long_installed_favorites_and_launch_survive_real_controller(self):
        if not os.access('/tools/node', os.X_OK): self.skipTest('Pinned system Node unavailable for UI parser integration')
        import importlib.util
        from unittest.mock import patch
        ids = ['app'+str(n).zfill(4)+'x'*175+'.desktop' for n in range(400)]
        for identity in ids: self.desktop(identity)
        spec = importlib.util.spec_from_file_location('app_reply_regression', BACKEND)
        assert spec is not None and spec.loader is not None
        backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
        with patch.dict(os.environ, self.env, clear=True):
            self.assertEqual({row['id'] for row in backend.app_rows(with_previews=False)[0]}, set(ids))
            for identity in ids[:-1]: backend.preferences.toggle('app-deck', backend.PREFERENCE_SCHEMA, identity)
        self.shim('gio')
        script = "const fs=require('fs'),vm=require('vm');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);console.log(JSON.stringify(c.actionResult(fs.readFileSync(0,'utf8'),0,0,'')));"
        for verb, identity in [('favorite', ids[-1]), ('launch', ids[0])]:
            result = subprocess.run(['/usr/bin/python3', str(BACKEND), 'action', '--id', identity, '--verb', verb, '--apply'], env=self.env, capture_output=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertGreater(len(result.stdout), 65536); self.assertLess(len(result.stdout), 1048576)
            self.assertEqual(json.loads(result.stdout)['preferences']['favorites'], ids)
            ui = subprocess.run(['/tools/node', '-e', script, str(BACKEND.parent/'Controller.js')], input=result.stdout, capture_output=True, timeout=4)
            self.assertEqual(ui.returncode, 0, ui.stderr)
            self.assertTrue(json.loads(ui.stdout)['ok'], ui.stdout)
            self.assertEqual(json.loads(ui.stdout)['preferences']['favorites'], ids)
            print('BACKEND_FIX_EVIDENCE '+json.dumps({'case':'400_favorites','verb':verb,'favorites':len(ids),'response_bytes':len(result.stdout),'ui_ok':True}), flush=True)
        self.assertEqual(len(self.calls()), 1)

    def test_104k_preference_write_and_ui_utf8_budget_boundary(self):
        if not os.access('/tools/node', os.X_OK): self.skipTest('Pinned system Node unavailable for UI parser integration')
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('app_preferences_reply_regression', BACKEND)
        assert spec is not None and spec.loader is not None
        backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
        with patch.dict(os.environ, self.env, clear=True):
            backend.preferences.change('app-deck', backend.PREFERENCE_SCHEMA, {'collections':{'Work':[str(n).zfill(4)+'x'*252 for n in range(400)]}})
        result = subprocess.run(['/usr/bin/python3', str(BACKEND), 'preferences', '--apply', '--payload', '{"options":{"category":"Development"}}'], env=self.env, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertGreater(len(result.stdout), 100000); self.assertLess(len(result.stdout), 110000)
        self.assertEqual(self.cli('preferences')['preferences']['options']['category'], 'Development')
        script = """const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const r=c.actionResult(fs.readFileSync(0,'utf8'),0,0,'');assert.equal(r.ok,true);assert.equal(r.preferences.options.category,'Development');
const base=JSON.stringify({ok:true,message:'Accepted',extra:'界'.repeat(100)}), limit=1048576;
const exact=base+' '.repeat(limit-Buffer.byteLength(base));assert.equal(Buffer.byteLength(exact),limit);
assert.equal(c.actionResult(exact,0,0,'').ok,true);assert.equal(c.actionResult(exact+' ',0,0,'').ok,false);
console.log('104k write and inclusive UTF-8 wire boundary accepted; overflow rejected');"""
        ui = subprocess.run(['/tools/node', '-e', script, str(BACKEND.parent/'Controller.js')], input=result.stdout, capture_output=True, timeout=4)
        self.assertEqual(ui.returncode, 0, ui.stderr)
        print('BACKEND_FIX_EVIDENCE '+json.dumps({'case':'104k_preferences_write','response_bytes':len(result.stdout),'ui_ok':True,'accepted_boundary_bytes':1048576,'rejected_boundary_bytes':1048577}), flush=True)


    def test_printed_maximum_reply_matches_wire_budget(self):
        if not os.access('/tools/node', os.X_OK): self.skipTest('Pinned system Node unavailable for UI parser integration')
        import importlib.util
        spec = importlib.util.spec_from_file_location('app_wire_preferences', BACKEND.parent / 'preferences.py')
        assert spec is not None and spec.loader is not None
        preferences = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preferences)
        value = {'ok': True, 'message': 'Accepted', 'extra': '界' * 100}
        value['extra'] += ' ' * (1048575 - len(preferences.encode_reply(value).encode('utf-8')))
        self.assertEqual(len(preferences.encode_reply(value).encode('utf-8')), 1048575)
        printer = "import json,sys;sys.path.insert(0,sys.argv[1]);import preferences;print(preferences.encode_reply(json.load(sys.stdin)))"
        printed = subprocess.run(['/usr/bin/python3', '-c', printer, str(BACKEND.parent)], input=json.dumps(value, ensure_ascii=False).encode('utf-8'), env=self.env, capture_output=True, timeout=4)
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertEqual(len(printed.stdout), 1048576)
        self.assertTrue(printed.stdout.endswith(b'\n'))
        script = "const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');let c={};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);const wire=fs.readFileSync(0,'utf8');assert.equal(Buffer.byteLength(wire),1048576);assert.equal(c.actionResult(wire,0,0,'').ok,true);assert.equal(c.actionResult(wire+' ',0,0,'').ok,false);"
        parsed = subprocess.run(['/tools/node', '-e', script, str(BACKEND.parent/'Controller.js')], input=printed.stdout, capture_output=True, timeout=4)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        value['extra'] += ' '
        with self.assertRaises(ValueError): preferences.encode_reply(value)

    def test_legacy_large_preferences_preserve_confirmed_action_and_usable_reads(self):
        # Seed an old valid store directly: new writes have a smaller aggregate budget.
        self.desktop(); self.shim('gio'); selected = 'sample.desktop'; verb = 'launch'
        prefs = self.cli('preferences')['preferences']
        prefs['collections'] = {'g'+str(g): [str(n).zfill(4)+'x'*252 for n in range(512)] for g in range(9)}
        base = Path(self.env.get('XDG_CONFIG_HOME', str(self.home/'.config')))
        store = base/'omarchy/lego/app-deck/preferences.json'
        store.parent.mkdir(parents=True, exist_ok=True)
        store.parent.parent.chmod(0o700); store.parent.chmod(0o700)
        raw = json.dumps(prefs, ensure_ascii=False, separators=(',', ':')).encode()
        self.assertGreater(len(raw), 1048576)
        store.write_bytes(raw); store.chmod(0o600)
        result = subprocess.run(['/usr/bin/python3', str(BACKEND), 'action', '--id', selected, '--verb', verb, '--apply'], env=self.env, capture_output=True, timeout=12)
        value = json.loads(result.stdout)
        self.assertTrue(self.calls())
        self.assertEqual(result.returncode, 0, value)
        self.assertTrue(value['ok'], value)
        self.assertIn('warning', value['message'].lower())
        self.assertLessEqual(len(result.stdout), 1048576)
        self.assertNotIn('preferences', value)
        for command in ('preferences',):
            result = subprocess.run(['/usr/bin/python3', str(BACKEND), command], env=self.env, capture_output=True, timeout=12)
            value = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, value)
            self.assertNotIn('preferences', value)
            self.assertIn('warning', value['message'].lower())
            self.assertLessEqual(len(result.stdout), 1048576)
            if command == 'catalog': self.assertIn(selected, [row['id'] for row in value['rows']])
        reset = subprocess.run(['/usr/bin/python3', str(BACKEND), 'preferences', '--apply', '--payload', '{"reset":true}'], env=self.env, capture_output=True, timeout=8)
        self.assertEqual(reset.returncode, 0, reset.stdout)
        self.assertEqual(json.loads(reset.stdout)['preferences']['collections'], {})


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

    def cli(self, *args, rc=0, artwork_opens=None):
        command = ['/usr/bin/python3',str(BACKEND),*args]
        if artwork_opens is not None:
            command = ['/usr/bin/python3','-c',
                       'import json,os,runpy,sys\n'
                       'opened=[]\n'
                       'def audit(event,args):\n'
                       ' if event == "open" and isinstance(args[0],str) and args[0].endswith((".png",".jpg",".jpeg")): opened.append(args[0])\n'
                       'sys.addaudithook(audit)\n'
                       'sys.argv=sys.argv[1:]\n'
                       'try: runpy.run_path(sys.argv[0],run_name="__main__")\n'
                       'finally: print(json.dumps(len(opened)),file=sys.stderr)\n',str(BACKEND),*args]
        r = subprocess.run(command,cwd='/',env=self.env,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        self.assertEqual(r.returncode,rc,r.stderr.decode()+r.stdout.decode())
        self.assertTrue(r.stdout.strip(), 'backend must emit JSON')
        self.assertLessEqual(len(r.stdout),1048576)
        self.assertNotIn(b'Traceback',r.stdout+r.stderr)
        if artwork_opens is not None:
            artwork_opens.append(json.loads(r.stderr))
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
            self.assertEqual(set(row)-{'id','label','detail','preview','color','category'},set())
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

    def test_launch_membership_skips_artwork_but_keeps_fresh_xdg_validation(self):
        import base64
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2kAAAAASUVORK5CYII=')
        icon=self.home/'.local/share/icons/hicolor/48x48/apps/sample.png'
        icon.parent.mkdir(parents=True);icon.write_bytes(png)
        paths=[self.desktop('named.desktop','Icon=sample\n'),
               self.desktop('absolute.desktop','Icon='+str(icon)+'\n'),
               self.desktop('missing.desktop','Icon=missing\n',base=self.home/'system')]
        self.desktop('hidden.desktop','Hidden=true\n')
        self.desktop('hidden.desktop','Icon=missing\n',base=self.home/'system')
        self.shim('gio')
        catalog_opens=[]
        rows={r['id']:r for r in self.cli('catalog',artwork_opens=catalog_opens)['rows']}
        self.assertEqual(set(rows),{p.name for p in paths})
        for name in ('named.desktop','absolute.desktop'):
            self.assertEqual(rows[name]['preview'],str(icon))
        self.assertNotIn('preview',rows['missing.desktop'])
        self.assertGreater(catalog_opens[0],0)
        action_opens=[]
        for path in paths:
            self.assertTrue(self.cli('action','--id',path.name,'--verb','launch','--apply',artwork_opens=action_opens)['ok'])
        self.assertEqual([c['argv'][1:] for c in self.calls()],[['launch',str(p)] for p in paths])
        paths[0].unlink()
        self.desktop('missing.desktop','Hidden=true\n')
        for ident in ('named.desktop','missing.desktop','hidden.desktop','../../bad.desktop'):
            self.assertFalse(self.cli('action','--id',ident,'--verb','launch','--apply',rc=1,artwork_opens=action_opens)['ok'])
        self.assertEqual(len(self.calls()),len(paths))
        print('ARTWORK_EVIDENCE '+json.dumps({'catalog_opens':catalog_opens,'action_opens':action_opens}),flush=True)
        self.assertEqual(action_opens,[0]*7)

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

    def test_desktop_actions_are_known_rows_delegated_to_gio_not_shell(self):
        path=self.desktop(extra='Categories=Office;Utility;\nActions=New;Private;\n[Desktop Action New]\nName=New Window\nExec=never-run --new\n[Desktop Action Private]\nName=Private Window\nExec=never-run --private\n')
        self.shim('gio',body='(Path(os.environ["HOME"])/"action-entry.txt").write_text(Path(sys.argv[2]).read_text())')
        rows={r['id']:r for r in self.cli('catalog')['rows']}
        self.assertIn('sample.desktop::New',rows)
        self.assertEqual(rows['sample.desktop::New']['category'],'Office')
        result=self.cli('action','--id','sample.desktop::New','--verb','launch','--apply')
        self.assertTrue(result['ok'])
        self.assertEqual(self.calls()[0]['argv'][1],'launch')
        dispatched=Path(self.calls()[0]['argv'][2])
        self.assertFalse(dispatched.exists(), 'temporary desktop file must be removed')
        content=(self.home/'action-entry.txt').read_text()
        self.assertIn('Exec = never-run --new',content)
        self.assertNotIn('--private',content)
        self.assertIn('not confirmed',result['message'])

    def test_unlisted_malformed_and_removed_desktop_actions_never_dispatch(self):
        path=self.desktop(extra='Actions=Good;Missing;Bad/Id;\n[Desktop Action Good]\nName=New\nExec=never-run --new\n[Desktop Action Unlisted]\nName=Hidden action\nExec=never-run --hidden\n')
        self.shim('gio')
        rows={r['id'] for r in self.cli('catalog')['rows']}
        self.assertEqual(rows,{'sample.desktop','sample.desktop::Good'})
        path.write_text(path.read_text().replace('Actions=Good;Missing;Bad/Id;','Actions='))
        for ident in ['sample.desktop::Good','sample.desktop::Unlisted','sample.desktop::Missing','sample.desktop::Bad/Id']:
            self.assertFalse(self.cli('action','--id',ident,'--verb','launch','--apply',rc=1)['ok'])
        self.assertEqual(self.calls(),[])

    def test_preferences_read_is_nonmutating_and_catalog_exposes_schema(self):
        result=self.cli('preferences')
        self.assertTrue(result['ok'])
        self.assertEqual(result['preferences']['version'],1)
        self.assertEqual(result['preferences']['favorites'],[])
        self.assertIn('view',result['settingsSchema'])
        cat=self.cli('catalog')
        self.assertEqual(cat['preferences'],result['preferences'])
        self.assertEqual(cat['settingsSchema'],result['settingsSchema'])
        self.assertFalse((self.home/'.config').exists())

    def test_preferences_patch_is_explicit_bounded_exact_json_and_persistent(self):
        payload=json.dumps({'options':{'view':'favorites'},'collections':{'Work':['inert-id']}})
        for args in [('preferences','--payload',payload),('catalog','--payload',payload),('preferences','--id','')]:
            self.assertFalse(self.cli(*args,rc=1)['ok'])
        result=self.cli('preferences','--payload',payload,'--apply')
        self.assertEqual(result['preferences']['options']['view'],'favorites')
        self.assertEqual(result['preferences']['collections'],{'Work':['inert-id']})
        before=self.cli('preferences')['preferences']
        for raw in ['[]','null','{"options":{"view":"all","view":"recent"}}','{"options":{"view":NaN}}','{"recent":[]}','{"options":{"command":"sh"}}','{"options":{"view":"bad"}}',json.dumps({'options':{'x':'界'*6000}})]:
            self.assertFalse(self.cli('preferences','--payload',raw,'--apply',rc=1)['ok'])
            self.assertEqual(self.cli('preferences')['preferences'],before)
        self.assertEqual(self.calls(),[])

    def test_preferences_reset_only_exact_true_resets_saved_preferences(self):
        self.cli('preferences','--payload','{"options":{"view":"recent"},"favorites":["inert-id"]}','--apply')
        for raw in ['{"reset":1}','{"reset":false}','{"reset":true,"favorites":[]}']:
            self.assertFalse(self.cli('preferences','--payload',raw,'--apply',rc=1)['ok'])
        result=self.cli('preferences','--payload','{"reset":true}','--apply')
        self.assertEqual(result['preferences']['options']['view'],'all')
        self.assertEqual(result['preferences']['favorites'],[])
        self.assertEqual(self.cli('preferences')['preferences'],result['preferences'])

    def test_favorite_toggle_validates_membership_without_helper(self):
        self.desktop();self.shim('gio')
        self.assertFalse(self.cli('action','--id','sample.desktop','--verb','favorite',rc=1)['ok'])
        self.assertFalse(self.cli('action','--id','unknown','--verb','favorite','--apply',rc=1)['ok'])
        result=self.cli('action','--id','sample.desktop','--verb','favorite','--apply')
        self.assertEqual(result['preferences']['favorites'],['sample.desktop'])
        self.assertTrue(self.cli('catalog')['rows'][0]['favorite'])
        self.assertEqual(self.cli('action','--id','sample.desktop','--verb','favorite','--apply')['preferences']['favorites'],[])
        self.assertEqual(self.calls(),[])

    def test_recent_records_only_accepted_launch_and_warns_on_storage_failure(self):
        self.desktop()
        args=('action','--id','sample.desktop','--verb','launch','--apply')
        self.shim('gio',rc=4)
        self.cli(*args,rc=1)
        self.assertEqual(self.cli('preferences')['preferences']['recent'],[])
        self.shim('gio')
        result=self.cli(*args)
        self.assertIn('preferences',result)
        self.assertEqual(result['preferences']['recent'],['sample.desktop'])
        pref=self.home/'.config/omarchy/lego/app-deck/preferences.json'
        pref.write_text('broken')
        result=self.cli(*args)
        self.assertTrue(result['ok'])
        self.assertIn('warning',result['message'].lower())
        self.assertEqual(pref.read_text(),'broken')

    def test_pinned_groups_favorites_and_recent_filter_without_authorizing_ids(self):
        self.desktop();self.desktop('other.desktop')
        self.cli('preferences','--payload','{"favorites":["sample.desktop","absent"],"collections":{"Work":["sample.desktop","absent"]},"options":{"view":"favorites"}}','--apply')
        self.assertEqual([r['id'] for r in self.cli('catalog')['rows']],['sample.desktop'])
        self.cli('preferences','--payload','{"options":{"view":"all","collection":"Work"}}','--apply')
        self.assertEqual([r['id'] for r in self.cli('catalog')['rows']],['sample.desktop'])
        self.shim('gio');self.cli('action','--id','other.desktop','--verb','launch','--apply')
        self.cli('preferences','--payload','{"options":{"view":"recent","collection":""}}','--apply')
        self.assertEqual([r['id'] for r in self.cli('catalog')['rows']],['other.desktop'])
        self.assertFalse(self.cli('action','--id','absent','--verb','launch','--apply',rc=1)['ok'])

    def test_category_filter_matches_all_categories_and_rejects_domain_garbage(self):
        self.desktop(extra='Categories=Office;Utility;\n')
        self.desktop('game.desktop','Categories=Game;\n')
        self.cli('preferences','--payload','{"options":{"category":"Utility"}}','--apply')
        self.assertEqual([r['id'] for r in self.cli('catalog')['rows']],['sample.desktop'])
        before=self.cli('preferences')['preferences']
        for value in ['../../bad','Office;Game','x'*65]:
            self.assertFalse(self.cli('preferences','--payload',json.dumps({'options':{'category':value}}),'--apply',rc=1)['ok'])
            self.assertEqual(self.cli('preferences')['preferences'],before)
        self.shim('gio')
        self.assertTrue(self.cli('action','--id','game.desktop','--verb','launch','--apply')['ok'])

    def test_real_gio_interprets_only_selected_desktop_action_offline(self):
        import time
        (self.bin/'gio').symlink_to('/usr/bin/gio')
        recorder=self.put('record.py','from pathlib import Path\nPath('+repr(str(self.home/'real-action.txt'))+').write_text("selected")\n')
        self.desktop(extra='Actions=New;\n[Desktop Action New]\nName=New Window\nExec=/usr/bin/python3 '+str(recorder)+'\n')
        result=self.cli('action','--id','sample.desktop::New','--verb','launch','--apply')
        self.assertTrue(result['ok'])
        output=self.home/'real-action.txt'
        deadline=time.monotonic()+2
        while not output.exists() and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertTrue(output.exists(),'actual GIO did not execute the selected harmless fixture')
        self.assertEqual(output.read_text(),'selected')
        self.assertEqual(result['preferences']['recent'],['sample.desktop::New'])

    def test_desktop_action_rows_respect_full_scan_membership_cap(self):
        import importlib.util
        for n in range(4095):
            self.desktop(f'app{n:04}.desktop')
        self.desktop('zzzz.desktop','Actions=New;\n[Desktop Action New]\nName=New\nExec=never-run\n')
        spec=importlib.util.spec_from_file_location('app_deck_cap',BACKEND)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from unittest.mock import patch
        with patch.dict(os.environ,self.env,clear=True):
            rows,paths=module.app_rows(with_previews=False)
        self.assertLessEqual(len(rows),4096)
        self.assertEqual(len(paths),len(rows))

    def test_all_shipped_python_sources_compile(self):
        sources=sorted(p for p in ROOT.rglob('*.py') if 'release-output' not in p.parts)
        for source in sources:
            compile(source.read_bytes(),str(source),'exec')
        self.assertGreaterEqual(len(sources),4)

    def test_inherited_default_exec_cannot_invent_a_declared_action(self):
        self.desktop(extra='Actions=New;\n[DEFAULT]\nExec=never-run --inherited\n[Desktop Action New]\nName=New Window\n')
        self.shim('gio')
        self.assertNotIn('sample.desktop::New',{r['id'] for r in self.cli('catalog')['rows']})
        self.assertFalse(self.cli('action','--id','sample.desktop::New','--verb','launch','--apply',rc=1)['ok'])
        self.assertEqual(self.calls(),[])

    def test_category_filter_does_not_expand_full_membership_beyond_scan_cap(self):
        for n in range(4096):
            self.desktop(f'app{n:04}.desktop','Categories=Office;\n')
        self.desktop('late.desktop','Categories=Game;\n',base=self.home/'system')
        self.cli('preferences','--payload','{"options":{"category":"Game"}}','--apply')
        self.assertEqual(self.cli('catalog')['rows'],[])

    def test_docs_reference_current_cli_and_only_existing_local_links(self):
        import re
        for name in ('README.md','BACKEND.md','NOTICE.backend.md'):
            text=(ROOT/name).read_text()
            self.assertNotIn('/home/leviathan',text)
            self.assertNotIn('/srv/projects',text)
            self.assertNotIn('runtime/0.2.1/',text)
            for target in re.findall(r'\]\(([^)]+)\)',text):
                if not target.startswith(('https://','http://','#')):
                    self.assertTrue((ROOT/target.split('#')[0]).is_file(),target)

if __name__ == '__main__':
    unittest.main()
