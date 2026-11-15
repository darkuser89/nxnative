"""Exercise the host/test Makefiles with a fake Metal compiler: deployment targets, toolchain recovery and incremental builds, without a GPU or game files."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import textwrap
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
GAME = '010015100B514000'
DRY = f'clang -O2 -c main.c -o gen/{GAME}/obj/host/main.c.o\nclang -O2 -o gen/{GAME}/dmhost gen/{GAME}/obj/host/main.c.o -framework Metal\ndsymutil gen/{GAME}/dmhost\n'   # make -n's host lines


def script(name):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / (name + '.py'))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostMSL(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('dm/host/Makefile', 'dm/mac/min_os.mk', 'dm/gfx/tests/Makefile', 'dm/gfx/tools/host_msl.py', f'dm/games/{GAME}/game.tsv'):
            dst = self.root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, dst)
        (self.root / 'dm/third_party/libvpx').mkdir(parents=True)
        self.source = self.root / 'dm/gfx/msl/clear.metal'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('kernel void clear() {}\n')
        tools = self.root / 'bin'
        tools.mkdir()
        compiler = tools / 'xcrun'
        compiler.write_text(f'#!{sys.executable}\n' + textwrap.dedent('''\
            import json, os, pathlib, sys, time
            args = sys.argv[1:]
            if '--show-sdk-path' in args:
                print(os.environ['TEST_METAL_SDK'])
            elif '--version' in args:
                version = os.environ['TEST_METAL_VERSION']
                if not version: sys.exit(1)
                print(version)
            else:
                if gate := os.environ.get('TEST_METAL_WAIT'):
                    until = time.monotonic() + 10
                    while not pathlib.Path(gate).exists():
                        if time.monotonic() > until: sys.exit('C compilation waited for unrelated Metal output')
                        time.sleep(0.01)
                with open(os.environ['TEST_METAL_LOG'], 'a') as f:
                    f.write(json.dumps(args) + '\\n')
                pathlib.Path(args[args.index('-o') + 1]).write_bytes(b'compiled library')
            '''))
        compiler.chmod(0o755)
        self.log = self.root / 'compiles.jsonl'
        self.env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                        TEST_METAL_VERSION='Metal test compiler 1', TEST_METAL_SDK='/sdk/1', TEST_METAL_LOG=str(self.log))
        self.header = self.root / 'dm/host/gen/msl-macosx/host_msl_gen.h'

    def make(self, *args, gfx=False):
        target = '../../host/gen/msl-macosx/host_msl_gen.h' if gfx else 'host_msl'
        p = subprocess.run(['make', '-s', '-C', 'dm/gfx/tests' if gfx else 'dm/host', f'GAME={GAME}', target, *args],
                           cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def compiles(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def host_dirs(self, dirs):
        for d in dirs: (self.root / d).mkdir(parents=True, exist_ok=True)
        (self.root / 'dm/games/game.py').touch()

    def test_mac_and_ios_use_their_deployment_targets(self):
        self.make()
        minimum = (self.root / 'dm/mac/min_os.mk').read_text().split('MACOS_MIN := ')[1].split()[0]
        self.assertIn(f'-mmacosx-version-min={minimum}', self.compiles()[-1])
        self.make('MSL_SDK=iphoneos')
        self.assertIn('-mios-version-min=26.0', self.compiles()[-1])
        self.assertIn('iphoneos', self.compiles()[-1])

    def test_compiler_installation_recovers_without_cleaning(self):
        self.env['TEST_METAL_VERSION'] = ''
        self.make()
        self.assertIn('#define HOST_MSL_COMPILER "none"', self.header.read_text())
        self.assertEqual(self.compiles(), [])
        self.env['TEST_METAL_VERSION'] = 'Metal test compiler 1'
        self.make()
        self.assertEqual(len(self.compiles()), 1)
        self.assertNotIn('#define HOST_MSL_COMPILER "none"', self.header.read_text())

    def test_gfx_tests_also_recheck_a_source_only_header(self):
        self.env['TEST_METAL_VERSION'] = ''
        self.make(gfx=True)
        self.env['TEST_METAL_VERSION'] = 'Metal test compiler 1'
        self.make(gfx=True)
        self.assertEqual(len(self.compiles()), 1)

    def test_unchanged_inputs_preserve_header_and_skip_compilation(self):
        self.make()
        before = self.header.stat().st_mtime_ns
        self.make()
        self.make(gfx=True)
        self.assertEqual(len(self.compiles()), 1)
        self.assertEqual(self.header.stat().st_mtime_ns, before)

    def test_compiler_sdk_target_and_source_changes_recompile(self):
        self.make()
        self.env['TEST_METAL_VERSION'] = 'Metal test compiler 2'
        self.make()
        self.assertEqual(len(self.compiles()), 2)
        self.env['TEST_METAL_SDK'] = '/sdk/2'
        self.make()
        self.assertEqual(len(self.compiles()), 3)
        self.make('MACOS_MIN=26.7')
        self.assertEqual(len(self.compiles()), 4)
        self.assertIn('-mmacosx-version-min=26.7', self.compiles()[-1])
        before = self.source.stat()
        self.source.write_text('kernel void clear_changed() {}\n')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.make('MACOS_MIN=26.7')
        self.assertEqual(len(self.compiles()), 5)

    def test_cold_c_compile_overlaps_metal_but_embedding_waits_for_header(self):
        self.host_dirs(['dm/host', 'dm/gfx/nvn'])
        (self.root / 'dm/host/main.c').write_text('int main(void) { return 0; }')
        (self.root / 'dm/gfx/nvn/host_msl.m').write_text('#include "host_msl_gen.h"')
        marker = self.root / 'main-started'
        self.env['TEST_METAL_WAIT'] = str(marker)
        compiler = self.root / 'bin/clang'
        compiler.write_text(f'#!{sys.executable}\n' + textwrap.dedent(f'''\
            import pathlib, sys
            out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
            header = pathlib.Path({str(self.header)!r})
            if 'main.c.o' in str(out): pathlib.Path({str(marker)!r}).write_text(str(header.exists()))
            else: assert header.is_file(), 'embedding compiled before its generated header'
            out.write_bytes(b'object')
            '''))
        compiler.chmod(0o755)
        targets = [f'gen/{GAME}/obj/host/main.c.o', f'gen/{GAME}/obj/gfx/nvn/host_msl.m.o']
        result = subprocess.run(['make', '-s', '-j2', f'GAME={GAME}', *targets], cwd=self.root / 'dm/host', env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(marker.read_text(), 'False', 'unrelated C work must start before the Metal header is ready')
        self.assertTrue(all((self.root / 'dm/host' / target).is_file() for target in targets))

    def test_regress_cache_refreshes_shaders_before_reusing_a_host(self):
        regress = script('regress')
        self.host_dirs(regress.HOST_DIRS)
        tree = NS(path=self.root, id='candidate')
        game = NS(id=GAME, env=lambda tree: self.env, profile_dir=lambda tree: self.root / f'dm/games/{GAME}')
        real_run = subprocess.run
        builds = []

        def run(cmd, **kw):
            if cmd[0] == 'make' and 'host_msl' not in cmd:
                if 'dmhost' in cmd:
                    builds.append(cmd)
                    (self.root / 'dm/host/dmhost').write_bytes(b'host executable')
                return NS(returncode=0)
            return real_run(cmd, **kw)

        with patch.object(regress, 'game_root', return_value=self.root / 'regress'), patch.object(regress, 'say'), patch.object(regress.subprocess, 'run', side_effect=run):
            self.env['TEST_METAL_VERSION'] = ''
            _, source_key, _ = regress.host_for(tree, game, 1)
            self.env['TEST_METAL_VERSION'] = 'Metal test compiler 1'
            _, compiled_key, _ = regress.host_for(tree, game, 1)
            self.assertNotEqual(compiled_key, source_key)
            self.assertEqual(regress.host_for(tree, game, 1)[1], compiled_key)
            self.assertEqual(len(builds), 2)
            self.source.write_text('kernel void changed() {}\n')
            _, changed_key, _ = regress.host_for(tree, game, 1)
            self.assertNotEqual(changed_key, compiled_key)
            tool = self.root / 'dm/gfx/tools/host_msl.py'
            tool.write_text(tool.read_text() + '\n# changed generator\n')
            self.assertNotEqual(regress.host_for(tree, game, 1)[1], changed_key)
            self.assertEqual(len(builds), 4)

    def test_nsp_packer_generates_and_fingerprints_the_header(self):
        nsp = script('build_ipa_from_nsp')
        self.host_dirs(nsp.HOST_DIRS)
        real_run = subprocess.run
        out = self.root / 'packed/dmhost'
        logs = self.root / 'logs'
        logs.mkdir()
        builds = []

        def run(cmd, log, env=None, cwd=None):
            if f'gen/{GAME}/dmhost' in cmd:   # the host itself (make gen/<id>/dmhost), which the script copies to out
                # A cold complete make also generates the header; subsequent key checks must see it.
                p = real_run(['make', '-s', f'GAME={GAME}', 'host_msl'], cwd=cwd, env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertTrue(self.header.is_file(), 'the completed host make generates the header the key fingerprints')
                builds.append(cmd)
                built = self.root / f'dm/host/gen/{GAME}/dmhost'; built.parent.mkdir(parents=True, exist_ok=True); built.write_bytes(b'host executable')
            else:
                self.assertIn('host_msl', cmd)
                p = real_run(['make', '-s', f'GAME={GAME}', 'host_msl'], cwd=cwd, env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        def command(cmd, **kw):
            return NS(returncode=0, stdout=DRY if cmd[0] == 'make' else 'test toolchain', stderr='')

        with patch.object(nsp, 'apple_silicon', return_value=True), patch.object(nsp, 'run', side_effect=run), patch.object(nsp.subprocess, 'run', side_effect=command):
            self.env['TEST_METAL_VERSION'] = ''
            nsp.build_mac_host(self.root, out, self.env, logs, NS(id=GAME))
            nsp.build_mac_host(self.root, out, self.env, logs, NS(id=GAME))
            self.assertEqual(len(builds), 1)
            self.env['TEST_METAL_VERSION'] = 'Metal test compiler 1'
            nsp.build_mac_host(self.root, out, self.env, logs, NS(id=GAME))
            self.assertEqual(len(builds), 2)
            self.source.write_text('kernel void changed() {}\n')
            nsp.build_mac_host(self.root, out, self.env, logs, NS(id=GAME))
            self.assertEqual(len(builds), 3)

    def test_decoder_identity_normalizes_only_chain_and_verifies_executable(self):
        nsp = script('build_ipa_from_nsp')
        self.host_dirs(nsp.HOST_DIRS)
        out = self.root / 'packed/dmhost'; logs = self.root / 'logs'; logs.mkdir()
        chain = self.root / nsp.CHAIN_H; chain.write_text('#define DMSH_CHAIN "aaaaaaaaaaaa"\n')
        decoder = self.root / 'dm/host/hle_oodle.c'; decoder.write_text('void decode(void) {}\n')
        profile = self.root / f'dm/host/gen/{GAME}/game_profile.h'; profile.parent.mkdir(parents=True); profile.write_text('#define HEAP 1\n')
        toolchain, builds = ['compiler 1'], []
        def run(cmd, log, env=None, cwd=None):
            if f'gen/{GAME}/dmhost' in cmd:
                builds.append(cmd)
                built = self.root / f'dm/host/gen/{GAME}/dmhost'; built.parent.mkdir(parents=True, exist_ok=True); built.write_bytes(f'host {len(builds)}'.encode())
        def command(cmd, **kw):
            return NS(returncode=0, stdout=DRY if cmd[0] == 'make' else toolchain[0], stderr='')
        with patch.object(nsp, 'apple_silicon', return_value=True), patch.object(nsp, 'run', side_effect=run), patch.object(nsp.subprocess, 'run', side_effect=command):
            def build(): return nsp.build_mac_host(self.root, out, self.env, logs, NS(id=GAME))
            build()
            record = out.with_name(out.name + '.decoder.json')
            self.assertTrue(record.is_file(), 'a managed host needs verified decoder provenance')
            first = nsp.decoder_host_key(out)
            self.assertIsNotNone(first)
            chain.write_text('#define DMSH_CHAIN "bbbbbbbbbbbb"\n'); build()
            self.assertEqual(nsp.decoder_host_key(out), first)
            self.assertEqual(len(builds), 2, 'the runtime host still rebuilds for a new chain')
            for change in (lambda: decoder.write_text('void decode(void) { fixed(); }\n'),
                           lambda: profile.write_text('#define HEAP 2\n'),
                           lambda: toolchain.__setitem__(0, 'compiler 2'),
                           lambda: chain.write_text(chain.read_text() + '#define OTHER_INPUT 1\n')):
                before = nsp.decoder_host_key(out)
                change(); build()
                self.assertNotEqual(nsp.decoder_host_key(out), before)
            before = nsp.decoder_host_key(out)
            out.write_bytes(b'replaced without rebuilding')
            self.assertIsNone(nsp.decoder_host_key(out))
            build(); self.assertEqual(nsp.decoder_host_key(out), before)
            record.write_text('[]')
            self.assertIsNone(nsp.decoder_host_key(out))
            build(); self.assertEqual(nsp.decoder_host_key(out), before)
            record.unlink()
            self.assertIsNone(nsp.decoder_host_key(out))
            build(); self.assertEqual(nsp.decoder_host_key(out), before)


class HostAndFonts(unittest.TestCase):
    def test_host_failure_cancels_background_fonts(self):
        nsp = script('build_ipa_from_nsp'); started = threading.Event(); stopped = []
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            def host(*args):
                self.assertTrue(started.wait(5)); raise nsp.BuildError('host failed')
            def fonts(py, repo, path, env, logs, cancel):
                started.set(); stopped.append(cancel.wait(5)); return work / 'font'
            with patch.object(nsp, 'build_mac_host', host), patch.object(nsp, 'build_shared_fonts', fonts), patch.object(nsp, 'PROG'):
                with self.assertRaisesRegex(nsp.BuildError, 'host failed'):
                    nsp.mac_host('python', REPO, work, {}, work / 'logs', NS(id=GAME))
            self.assertEqual(stopped, [True])
            self.assertFalse((work / 'logs/host_font_timing.json').exists())

    def test_independent_builds_overlap_and_write_separate_wall_times(self):
        nsp = script('build_ipa_from_nsp')
        with tempfile.TemporaryDirectory() as d:
            work = Path(d); barrier = threading.Barrier(2); budget = []
            def host(*args):
                barrier.wait(timeout=5); return work / 'tools/dmhost'
            def fonts(py, repo, path, env, logs, cancel=None):
                budget.append(env['J']); barrier.wait(timeout=5); return work / 'font'
            with patch.object(nsp, 'build_mac_host', host), patch.object(nsp, 'build_shared_fonts', fonts), patch.object(nsp, 'PROG'):
                result = nsp.mac_host('python', REPO, work, {'J': '8'}, work / 'logs', NS(id=GAME))
            self.assertEqual(result, (work / 'tools/dmhost', work / 'font'))
            self.assertEqual(budget, ['1'], 'cold make owns the CPU budget; fonts overlap on one worker')
            timing = json.loads((work / 'logs/host_font_timing.json').read_text())
            self.assertTrue(timing['parallel'])
            self.assertGreaterEqual(timing['total_seconds'], max(timing['host_seconds'], timing['fonts_seconds']))
            self.assertGreaterEqual(timing['child_cpu_seconds'], 0)

    def test_font_failure_propagates_and_does_not_leave_a_success_record(self):
        nsp = script('build_ipa_from_nsp')
        with tempfile.TemporaryDirectory() as d:
            work = Path(d); logs = work / 'logs'; logs.mkdir()
            (logs / 'host_font_timing.json').write_text('old record')
            with patch.object(nsp, 'build_mac_host', return_value=work / 'tools/dmhost'), patch.object(nsp, 'build_shared_fonts', side_effect=nsp.BuildError('font failed')), patch.object(nsp, 'PROG') as progress:
                with self.assertRaisesRegex(nsp.BuildError, 'font failed'):
                    nsp.mac_host('python', REPO, work, {}, logs, NS(id=GAME))
                progress.end.assert_not_called()
            self.assertFalse((logs / 'host_font_timing.json').exists())


if __name__ == '__main__':
    unittest.main()
