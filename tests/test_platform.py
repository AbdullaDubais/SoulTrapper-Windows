"""Platform guards: every Windows-specific choice has a Mac equivalent.

The port was Windows-only.  These tests hold the guarantee that no game module
branches on ``sys.platform`` itself, that both platforms' equivalents resolve -
the NVDA DLL its VoiceOver backend, ``soft_oal.dll`` its ``libopenal.dylib``,
the ``MessageBoxW`` startup failure an ``osascript`` alert - and that the Mac
bundle the build script writes is a bundle Finder will actually open.

Plain functions, no runner needed::

    python tests/test_platform.py

Tests that need the game's own audio or map data are not here: this file has to
run on a fresh clone, where ``assets/`` and ``data/`` are not present.
"""

import ast
import os
import plistlib
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import host                                              # noqa: E402

VENDOR_OPENAL_WIN = os.path.join(ROOT, 'vendor', 'openal', 'soft_oal.dll')
VENDOR_OPENAL_MAC = os.path.join(ROOT, 'vendor', 'openal-mac',
                                 'libopenal.dylib')
VENDOR_NVDA = os.path.join(ROOT, 'vendor', 'nvda',
                           'nvdaControllerClient64.dll')

#: Modules that make a platform choice, and so are allowed to ask host.py.
GUARDED = ('host.py', 'speech.py', 'spatial_audio.py', 'saves.py',
           'crash_report.py', 'game.py')


# ------------------------------------------------------------ platform module
def test_platform_is_decided_once():
    assert host.PLATFORM in ('windows', 'mac', 'other')
    assert host.WINDOWS == (sys.platform == 'win32')
    assert host.MAC == (sys.platform == 'darwin')
    # exactly one of the two supported platforms, never both
    assert not (host.WINDOWS and host.MAC)


def test_quit_hint_is_the_platforms_window_close():
    assert host.quit_hint() == ('Cmd+Q' if host.MAC else
                                'Alt+F4' if host.WINDOWS else 'window close')


def test_speech_backend_order_follows_the_platform():
    if host.MAC:
        assert host.speech_backend_order() == ['voiceover', 'null']
    elif host.WINDOWS:
        assert host.speech_backend_order() == ['nvda', 'sapi', 'null']


def test_version_comes_from_the_version_file():
    want = open(os.path.join(ROOT, 'VERSION'), encoding='utf8').read().strip()
    assert host.VERSION == (want or '1.0')
    assert host.VERSION in open(os.path.join(ROOT, 'game.py'),
                                encoding='utf8').read(), (
        'game.py names the version too; keep the two in step')


def test_the_mac_bundle_cannot_disagree_with_the_game_about_the_version():
    """VERSION exists to stamp the .app's Info.plist; the game has its own copy.

    They are separate, so a release that bumps one and forgets the other
    produces a bundle that calls itself 1.0 while the window inside it says 1.1.
    Nothing errors: Finder just shows the wrong version in Get Info.
    """
    for rel in ('game.py', 'crash_report.py'):
        source = open(os.path.join(ROOT, rel), encoding='utf8').read()
        assert f'Soul Trapper {host.VERSION}' in source, \
            f'{rel} does not say "Soul Trapper {host.VERSION}"; if the version ' \
            'was bumped, VERSION has to be bumped with it'


def inspect_source(fn):
    import inspect
    return inspect.getsource(fn)


def code_of(fn):
    """A function's body, docstring and signature removed."""
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect_source(fn)))
    body = tree.body[0].body if isinstance(tree.body[0], ast.FunctionDef) \
        else tree.body
    if body and isinstance(body[0], ast.Expr):
        body = body[1:]
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def test_mac_app_bundle_detection_looks_at_the_path():
    """Not at sys.frozen: the Mac build runs a real interpreter over game.py."""
    if not host.MAC:
        return
    assert host.in_mac_app_bundle() is False      # this checkout is not in one
    body = code_of(host.in_mac_app_bundle)
    assert 'frozen' not in body, 'in_mac_app_bundle checks sys.frozen'
    assert 'Contents' in body


# ---------------------------------------------------------------- openal paths
def test_openal_library_is_the_platforms_own():
    lib = host.openal_library(ROOT)
    if host.MAC:
        assert lib.name == 'libopenal.dylib'
        assert lib.parent.name == 'openal-mac'
    else:
        assert lib.name == 'soft_oal.dll'
        assert lib.parent.name == 'openal'
    assert lib.is_file(), lib


def test_the_vendor_library_is_preferred_over_a_bare_one():
    """The Windows ZIP keeps it in vendor/, and that must keep winning.

    A bare copy at the root is the fallback for a layout that flattens it, not
    a reason to prefer it: changing the order would quietly change which
    library an existing Windows install loads.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / host.openal_bare_name()).write_bytes(b'')
        assert host.openal_library(root) == root / host.openal_bare_name()
        vendor = root / host.openal_library_name()
        vendor.parent.mkdir(parents=True)
        vendor.write_bytes(b'')
        assert host.openal_library(root) == vendor, 'vendor/ must win'
    with tempfile.TemporaryDirectory() as tmp:
        assert host.openal_library(tmp) == \
            Path(tmp) / host.openal_library_name(), 'and is where it reports it missing'


def test_the_mac_dylib_is_committed_beside_the_ignored_vendor_tree():
    """It cannot be downloaded on a player's machine, so it has to be in git."""
    assert os.path.exists(VENDOR_OPENAL_MAC), VENDOR_OPENAL_MAC
    out = os.popen('git check-ignore "%s"' % VENDOR_OPENAL_MAC).read().strip()
    assert not out, 'libopenal.dylib is gitignored: ' + out


def test_mac_dylib_is_a_real_mach_o_for_this_machine():
    import subprocess
    out = subprocess.run(['file', VENDOR_OPENAL_MAC],
                         capture_output=True, text=True).stdout
    assert 'Mach-O' in out, out
    if host.MAC:
        machine = 'arm64' if os.uname().machine == 'arm64' else 'x86_64'
        assert machine in out, out


def test_loading_the_library_sets_the_platforms_search_path():
    """dyld on the Mac, add_dll_directory on Windows - both before ctypes."""
    source = inspect_source(host.prepare_library_path)
    if host.MAC:
        assert 'DYLD_FALLBACK_LIBRARY_PATH' in source
    else:
        assert 'add_dll_directory' in source


def test_spatial_audio_loads_its_library_through_host():
    source = open(os.path.join(ROOT, 'spatial_audio.py'), encoding='utf8').read()
    assert 'vendor/openal/soft_oal.dll' not in source
    assert 'openal_library' in source


def test_openal_reports_hrtf_on_both_platforms():
    """The whole game is the HRTF; if it will not engage, nothing is audible
    in the right place, so this is worth failing loudly rather than quietly."""
    from spatial_audio import SpatialAudio
    audio = SpatialAudio(ROOT)
    try:
        assert audio.version.startswith('1.1 '), audio.version
        assert audio.hrtf is True, 'OpenAL refused stereo headphone HRTF'
    finally:
        audio.close()


# -------------------------------------------------------------------- speech
def test_speech_picks_a_backend_the_platform_allows():
    from speech import Speech
    s = Speech(ROOT)
    try:
        if host.MAC:
            assert s.backend.name in ('voiceover', 'null'), s.backend.name
        else:
            assert s.backend.name in ('nvda', 'sapi', 'null'), s.backend.name
    finally:
        s.stop()


def test_a_missing_backend_falls_through_to_the_next_one():
    """pyobjc absent, no NVDA: the game must still run, just silently."""
    from speech import Speech
    s = Speech(ROOT)
    s.say('a menu item')
    assert s.last == 'a menu item'
    assert s.backend is not None


def test_silent_speech_says_nothing_but_still_records():
    from speech import Speech
    s = Speech(ROOT, silent=True)
    s.say('not spoken')
    assert s.last == 'not spoken'
    s.stop()


def test_speech_names_the_mac_backend():
    from speech import VoiceOverSpeech, BACKENDS
    assert VoiceOverSpeech.name == 'voiceover'
    assert set(BACKENDS) == {'voiceover', 'nvda', 'sapi', 'null'}


def test_nvda_is_only_chosen_when_nvda_is_actually_running():
    """The DLL loads whether NVDA is running or not.

    An installed-but-stopped NVDA used to fall through to SAPI.  Choosing it
    anyway would leave a Windows player with no speech at all, which is the one
    failure this refactor must not introduce.
    """
    import speech
    source = inspect_source(speech.NvdaSpeech.__init__)
    assert 'testIfRunning' in source, \
        'NvdaSpeech must check NVDA is running, or SAPI never gets its turn'
    try:
        speech.NvdaSpeech(ROOT)          # no DLL here, or no NVDA running
        running = True
    except Exception:
        running = False
    if not running:
        chosen = speech.Speech(ROOT).backend.name
        assert chosen != 'nvda', 'a stopped NVDA was chosen as the backend'


def test_a_backend_that_will_not_start_is_skipped_not_half_used():
    """Every backend takes (root); one that raises must not be the choice."""
    import speech
    order = speech.BACKENDS

    class Refuses:
        name = 'voiceover'
        def __init__(self, root):
            raise OSError('not available here')

    saved = dict(order)
    order['voiceover'] = Refuses
    try:
        s = speech.Speech(ROOT)
        assert s.backend.name == 'null', s.backend.name
        s.say('still recorded')
        assert s.last == 'still recorded'
    finally:
        speech.BACKENDS.clear()
        speech.BACKENDS.update(saved)


def test_voiceover_module_matches_the_csharp_interfaces():
    """VoiceOverOutput.cs -> voiceover.py, call for call."""
    import voiceover
    for fn in ('is_supported', 'is_running', 'speak', 'cancel',
               'is_speaking', 'shutdown'):
        assert callable(getattr(voiceover, fn, None)), fn
    # pyobjc is a Mac-only dependency and may simply not be installed; what
    # matters is that is_supported() tells the truth about that rather than
    # claiming VoiceOver and then failing to speak.
    assert voiceover.is_supported() == (host.MAC and pyobjc_available())
    if not host.MAC:
        assert voiceover.speak('no-op off the mac') is False


def pyobjc_available():
    try:
        import Foundation                                    # noqa: F401
        import AppKit                                        # noqa: F401
        return True
    except ImportError:
        return False


def test_the_applescript_is_escaped_the_way_the_csharp_escapes_it():
    from voiceover import _escape_applescript
    assert _escape_applescript('say "hello"') == 'say \\"hello\\"'
    assert _escape_applescript('back\\slash') == 'back\\\\slash'


# --------------------------------------------------------------------- paths
def test_user_data_dir_is_the_platforms_own():
    d = host.user_data_dir()
    if host.MAC:
        assert d.parts[-3:] == ('Library', 'Application Support',
                                'Soul Trapper'), d
    elif host.WINDOWS:
        assert os.environ.get('LOCALAPPDATA') in str(d)
        assert d.name == 'SoulTrapperWindows', d


def test_saves_default_to_user_data_not_the_bundle():
    """A .app is usually somewhere nothing inside it can be written to."""
    from saves import default_root
    if not host.MAC:
        return
    assert default_root() == host.user_data_dir()


def test_a_source_checkout_is_not_portable_on_either_platform():
    """This repository is not _internal, so saves go to the per-user folder."""
    assert not host.is_portable(ROOT)


def test_the_internal_folder_alone_makes_it_portable_only_on_windows():
    """_internal is how the Windows ZIP is recognised, not a request.

    This is the Windows build's existing rule, exactly: a game folder called
    _internal keeps its saves beside the executable.  The Mac shares the
    folder and not the conclusion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        internal = Path(tmp) / '_internal'
        internal.mkdir()
        assert host.is_portable(internal) == host.WINDOWS
        # and the other way round: any other folder name is not _internal
        other = Path(tmp) / 'something_else'
        other.mkdir()
        assert host.is_portable(other) is False


def test_portable_marker_is_honoured_on_either_platform():
    """It sits beside the executable, one level up from _internal."""
    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / 'Soul Trapper'
        internal = game / '_internal'
        internal.mkdir(parents=True)
        assert not host.is_portable(internal)
        (game / 'portable.txt').write_text('')
        assert host.is_portable(internal)


def test_a_copy_of_the_game_inside_a_bundle_is_not_portable():
    """The bundle has _internal too, and /Applications cannot be written to."""
    if not host.MAC:
        return
    with tempfile.TemporaryDirectory() as tmp:
        internal = Path(tmp) / 'Play Soul Trapper.app' / 'Contents' / \
            'Resources' / 'Soul Trapper' / '_internal'
        internal.mkdir(parents=True)
        assert not host.is_portable(internal)


# --------------------------------------------------------- module-level guards
def test_windows_only_imports_never_load_off_windows():
    """WinDLL / windll / winreg must sit behind a platform guard.

    Only *module-level* imports count: an import inside a function never runs
    unless that function is called, which is how the guarded ones are written.
    """
    for rel in GUARDED:
        tree = ast.parse(open(os.path.join(ROOT, rel),
                              encoding='utf8').read())
        for node in tree.body:                          # module level only
            if isinstance(node, ast.Import):
                assert all(a.name != 'winreg' for a in node.names), rel
            elif isinstance(node, ast.ImportFrom):
                assert node.module != 'winreg', rel


def test_no_game_module_branches_on_sys_platform():
    """Everything platform-specific goes through host.py."""
    for rel in ('audio.py', 'engine.py', 'spatial_audio.py', 'speech.py',
                'saves.py', 'crash_report.py', 'game.py', 'sound_levels.py'):
        source = open(os.path.join(ROOT, rel), encoding='utf8').read()
        assert 'sys.platform' not in source, rel
        assert 'platform.win32' not in source, rel


def without_comments(path):
    """A module's source with its comments stripped.

    Naming the thing you are not allowed to name is the whole point of the
    comment beside it, so a test that read comments too would forbid the
    explanation.  Comments go; code does not.
    """
    import tokenize
    out = []
    with open(path, 'rb') as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type != tokenize.COMMENT:
                out.append(tok.string)
    return ' '.join(out)


def test_no_game_module_names_a_windows_library_or_environment():
    """Nothing reaches past host.py for a platform decision.

    speech.py is the one exception, and it is the exception by design: it *is*
    the screen-reader backends, so the NVDA backend names the NVDA DLL and
    nothing else does.  What it may not do is decide to use it - which is
    host.speech_backend_order(), and is covered above.
    """
    everywhere = ('soft_oal.dll', 'LOCALAPPDATA', 'user32', 'windll',
                  'nvdaControllerClient64.dll')
    for rel in GUARDED:
        if rel == 'host.py':
            continue
        needles = everywhere
        if rel == 'speech.py':
            needles = tuple(n for n in everywhere
                            if n != 'nvdaControllerClient64.dll')
        source = without_comments(os.path.join(ROOT, rel))
        for needle in needles:
            assert needle not in source, f'{rel} still names {needle}'


def test_host_names_both_platforms_equivalents():
    """Otherwise a platform has quietly lost its OpenAL and its speech."""
    source = open(os.path.join(ROOT, 'host.py'), encoding='utf8').read()
    for needle in ('soft_oal.dll', 'libopenal.dylib', 'nvda',
                   'voiceover', 'SoulTrapperWindows', 'Soul Trapper'):
        assert needle in source, f'host.py does not mention {needle}'


# ------------------------------------------------------------------- failure
def test_error_reports_go_somewhere_writable():
    """The bundle usually is not; the per-user folder is."""
    import crash_report
    source = inspect_source(crash_report.write_error)
    assert 'user_data_dir' in source
    assert 'user32' not in source


def test_a_log_is_not_buried_inside_the_app_bundle():
    """Finder shows a .app as one file, so a log inside it cannot be sent."""
    import crash_report
    source = code_of(crash_report.write_error)
    assert 'in_mac_app_bundle' in source
    assert 'is_portable' in source, \
        'a deliberately portable copy should still log beside the game'


def test_the_read_only_bundle_falls_back_to_the_per_user_folder():
    """End to end, in a bundle that genuinely cannot be written to."""
    import shutil
    import subprocess
    import tempfile
    if not host.MAC:
        return
    tmp = Path(tempfile.mkdtemp())
    bundle = tmp / 'Play Soul Trapper.app' / 'Contents' / 'Resources' / \
        'Soul Trapper' / '_internal'
    bundle.mkdir(parents=True)
    for name in ('crash_report.py', 'host.py', 'saves.py', 'VERSION'):
        shutil.copy2(Path(ROOT) / name, bundle / name)
    for folder in (bundle, bundle.parent, bundle.parent.parent):
        os.chmod(folder, 0o555)                    # a read-only mount, more or less
    home = tmp / 'home'
    home.mkdir()
    code = ('import host, crash_report\n'
            'assert host.in_mac_app_bundle()\n'
            'try:\n'
            '    raise RuntimeError("deliberate")\n'
            'except RuntimeError:\n'
            '    print(crash_report.write_error("read-only bundle"))\n')
    done = subprocess.run([sys.executable, '-c', code], cwd=str(bundle),
                          capture_output=True, text=True,
                          env=dict(os.environ, HOME=str(home)))
    assert done.returncode == 0, done.stderr
    log = Path(done.stdout.strip())
    assert log.is_file(), done.stdout + done.stderr
    assert log.parent == (home / 'Library' / 'Application Support'
                          / 'Soul Trapper'), log
    assert 'RuntimeError' in log.read_text()
    assert not (bundle.parent / 'error.log').exists(), \
        'nothing should have been written into the bundle'


def test_the_startup_dialog_is_the_platforms_own():
    """MessageBoxW on Windows, an osascript alert on the Mac."""
    source = open(os.path.join(ROOT, 'game.py'), encoding='utf8').read()
    assert 'MessageBoxW' not in source
    assert 'host.alert(' in source
    if host.MAC:
        assert 'osascript' in inspect_source(host.alert)


# ------------------------------------------------------------------ build Mac
def test_the_build_script_refuses_to_run_off_the_mac():
    import tools.build_mac_app as build
    source = open(os.path.join(ROOT, 'tools', 'build_mac_app.py'),
                  encoding='utf8').read()
    assert 'host.MAC' in source
    assert build.APP_NAME == 'Play Soul Trapper'


def test_the_mac_bundle_identifier_is_reverse_dns():
    import tools.build_mac_app as build
    bid = build.BUNDLE_ID
    assert bid.count('.') >= 2         # com.<something>.<leaf>: not 'Play ...'
    assert ' ' not in bid
    assert all(part.isalnum() for part in bid.split('.')), bid


def test_the_bundle_is_stamped_with_identity_and_version():
    """A bundle whose Info.plist says 0.0.0 ships as 0.0.0 to Spotlight."""
    import tools.build_mac_app as build
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, 'game')
        internal = os.path.join(source, '_internal')
        os.makedirs(internal)
        for name in ('game.py', 'host.py'):
            open(os.path.join(internal, name), 'w').close()
        for name in ('assets', 'data', 'vendor'):
            os.makedirs(os.path.join(source, name))
        bundle = build.build(source, 'sys', tmp, portable=True)
        with open(os.path.join(bundle, 'Contents', 'Info.plist'), 'rb') as fh:
            plist = plistlib.load(fh)
        assert plist['CFBundleIdentifier'] == build.BUNDLE_ID
        assert plist['CFBundleShortVersionString'] == host.VERSION
        assert plist['CFBundleVersion'] == host.VERSION
        assert plist['CFBundleExecutable'] == build.APP_NAME

        # And it is a bundle, not a folder of files: PkgInfo, an executable
        # launcher, and the game where the launcher expects it.
        assert open(os.path.join(bundle, 'Contents', 'PkgInfo')).read() \
            == 'APPL????'
        launcher = os.path.join(bundle, 'Contents', 'MacOS', build.APP_NAME)
        assert os.access(launcher, os.X_OK), launcher
        body = open(launcher, encoding='utf8').read()
        assert body.startswith('#!/bin/bash')
        assert '--launcher' in body, 'the launcher owns the crash dialog'
        assert '_internal/game.py' in body
        assert os.path.isfile(os.path.join(bundle, 'Contents', 'Resources',
                                           'Soul Trapper', '_internal',
                                           'game.py'))
        assert os.path.isfile(os.path.join(bundle, 'Contents', 'Resources',
                                           'Soul Trapper', 'portable.txt'))


def test_the_launcher_reports_the_exit_code_the_way_launcher_cs_does():
    import tools.build_mac_app as build
    body = build.LAUNCHER.format(game='Soul Trapper', python='/x/python3',
                                 version=host.VERSION)
    assert 'exit $STATUS' in body
    assert 'error.log' in body
    assert 'osascript' in body


def test_the_game_tree_is_copied_the_way_the_windows_zip_is():
    """Only _internal/, because that is where the game resolves its own paths.

    game.py sets ROOT to its own folder, and audio.py, spatial_audio.py and the
    credits reader all build on that - so assets/, data/ and vendor/ are inside
    _internal/ in the Windows ZIP. Copying them as siblings as well would put a
    second, unused copy of the game's audio in the bundle.
    """
    import tools.build_mac_app as build
    assert build.COPIED == ('_internal',)
    assert 'runtime' not in build.COPIED, 'the runtime is staged separately'
    assert build.EXPECTED == ('assets', 'data', 'vendor')


def test_the_runtime_sits_beside_internal_like_the_windows_zip():
    """Launcher.cs starts <root>/runtime/python.exe, where root is _internal's
    parent - and the portable saves folder is that same root."""
    import tools.build_mac_app as build
    body = build.LAUNCHER.format(game='Soul Trapper', python='/x', version='1.0')
    assert '"$ROOT/runtime/bin/python3"' in body
    assert '"$ROOT/_internal/game.py"' in body
    assert 'LOGDIR="$ROOT"' in body, 'and it can fall back to that same root'


def test_building_from_a_folder_missing_host_py_is_refused():
    """An older prepared folder would fail at the first import instead."""
    import tools.build_mac_app as build
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'game'
        (source / '_internal').mkdir(parents=True)
        (source / '_internal' / 'game.py').write_text('')
        for name in ('assets', 'data', 'vendor'):
            (source / name).mkdir()
        try:
            build.build(str(source), 'sys', str(Path(tmp) / 'out'), False)
        except SystemExit as e:
            assert 'host.py' in str(e), e
        else:
            raise AssertionError('a folder with no host.py was accepted')


# ------------------------------------------------------------------- the uv one
def test_one_flag_is_enough_to_build():
    """The usual case is --source and nothing else.

    That only works because the runtime is built by default, which is the whole
    reason --runtime defaults to uv: a flag you have to look up is a flag you
    do not find.
    """
    import tools.build_mac_app as build
    args = build.parser().parse_args(['--source', 'some/game/folder'])
    assert args.runtime == 'uv', 'the runtime should need no thought'
    assert args.portable is False
    assert args.python == '', 'and neither should its version'
    assert args.out.endswith('dist')


def test_source_is_the_only_thing_that_is_required():
    import tools.build_mac_app as build
    for argv in ([], ['--runtime', 'sys']):
        try:
            build.parser().parse_args(argv)
        except SystemExit:
            continue
        raise AssertionError(f'{argv} was accepted without --source')


def test_python_only_applies_to_a_runtime_uv_builds():
    import tools.build_mac_app as build
    ok = build.parser().parse_args(['--source', 'g', '--python', '3.12'])
    assert ok.python == '3.12'
    # And main() refuses it rather than silently ignoring it.
    assert '--python only applies' in inspect_source(build.main)


def test_main_reaches_the_build_rather_than_failing_on_its_own_arguments():
    """main() has to parse before it can build, and the order is easy to break.

    A duplicated line once sat here, and nothing caught it: --help exits inside
    argparse before reaching it, and the tests only ever called parser() rather
    than main(). So run main() for real, against a folder that is not there, and
    require the failure to be about the folder.
    """
    import tools.build_mac_app as build
    if not host.MAC:
        return
    out = Path(tempfile.mkdtemp())
    saved = sys.argv
    sys.argv = ['build_mac_app.py', '--source', str(out / 'no-such-game'),
                '--out', str(out)]
    try:
        build.main()
    except SystemExit as e:
        assert 'no such source folder' in str(e), \
            f'main() failed before it tried to build: {e}'
    except Exception as e:                            # noqa: BLE001
        raise AssertionError(f'main() raised {e!r} rather than building')
    else:
        raise AssertionError('main() reported success on a missing folder')
    finally:
        sys.argv = saved


def test_the_runtime_script_gets_its_site_packages_path_from_the_interpreter():
    """Not by string surgery on the version.

    python3.12 has no patch component, so ``${VERSION%.*}`` is "3" and the
    packages land in lib/python3 instead of lib/python3.12 - which the build
    script caught by failing to import them. Ask the interpreter.
    """
    script = Path(ROOT) / 'tools' / 'build_mac_runtime.sh'
    body = script.read_text(encoding='utf8')
    assert 'python${VERSION%.*}' not in body
    assert 'sys.version_info[:2]' in body


def test_the_runtime_script_does_not_ship_the_dev_group():
    """pyflakes is for working on the game, not for shipping it."""
    body = (Path(ROOT) / 'tools' / 'build_mac_runtime.sh').read_text(
        encoding='utf8')
    assert '--no-default-groups' in body
    assert '--frozen' in body, 'and at the versions uv.lock pins'


def test_a_virtualenv_is_never_what_gets_carried():
    """It cannot be: pyvenv.cfg names an absolute interpreter.

    So the runtime script builds one only to merge its site-packages, and the
    build script says so when handed something that turns out to be one.
    """
    body = (Path(ROOT) / 'tools' / 'build_mac_runtime.sh').read_text(
        encoding='utf8')
    import tools.build_mac_app as build
    assert 'pyvenv.cfg' in body, 'and the reason should be written down'
    assert 'site-packages' in body, 'what it takes from a virtualenv'
    assert 'pyvenv.cfg' in code_of(build.resolve_python), \
        'and the build script refuses one, saying why'


def test_dependencies_come_from_the_lockfile_and_pyobjc_is_mac_only():
    toml = (Path(ROOT) / 'pyproject.toml').read_text(encoding='utf8')
    assert 'pygame-ce' in toml
    assert "sys_platform == 'darwin'" in toml, \
        'pyobjc must not be installed on Windows'
    # audioop is gone from 3.13; the runtime is pinned to 3.12, and the marker
    # is what lets a developer on a newer interpreter still work.
    assert "python_version >= '3.13'" in toml
    assert (Path(ROOT) / 'uv.lock').is_file(), \
        'a shipping runtime wants pinned versions'


def test_the_runtime_defaults_to_the_last_python_with_audioop():
    """3.12, and the same one the Windows build uses."""
    body = (Path(ROOT) / 'tools' / 'build_mac_runtime.sh').read_text(
        encoding='utf8')
    assert 'VERSION="${2:-3.12}"' in body
    import tools.build_mac_app as build
    assert '3.12' in inspect_source(build.parser), \
        'and --help should say so, not just the script'


def test_both_platforms_are_pinned_to_the_same_python():
    """The point of 3.12: the two runtimes behave identically."""
    import sound_levels
    version = sys.version_info[:2]
    if version == (3, 12):
        assert sound_levels.audioop is not None, \
            '3.12 has audioop in the standard library'


# --------------------------------------------------------------- dependencies
def local_modules():
    return {p.stem for p in Path(ROOT).glob('*.py')}


def third_party_imports():
    """Every non-stdlib, non-local module the game imports at any depth.

    Walked rather than listed: engine.py is imported by game.py, so the
    dependency is one level down and easy to miss by reading only the entry
    point.  That is exactly how unicorn came to be missing.
    """
    import ast
    stdlib = set(sys.stdlib_module_names)
    mine = local_modules()
    found = {}
    for path in sorted(Path(ROOT).glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf8'))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split('.')[0]
                if top in stdlib or top in mine:
                    continue
                found.setdefault(top, set()).add(path.name)
    return found


#: Module name -> the distribution that provides it, where the two differ.
#: These three are the exceptions; every other module matches its distribution.
DISTRIBUTION = {
    'pygame': 'pygame-ce',
    'Foundation': 'pyobjc-framework-cocoa',
    'AppKit': 'pyobjc-framework-cocoa',
    'audioop': 'audioop-lts',   # stdlib on 3.12, this drop-in from 3.13
}


def distribution_of(module):
    return DISTRIBUTION.get(module, module)


def declared_distributions():
    """The runtime dependencies pyproject.toml declares, by name.

    Only ``[project].dependencies``: the dev group is for working on the game
    and nothing in the game imports it.  Read with tomllib rather than a regex,
    because a regex over the file also finds ``version = "1.0"``.
    """
    import tomllib
    with open(Path(ROOT) / 'pyproject.toml', 'rb') as fh:
        project = tomllib.load(fh)['project']
    names = set()
    for requirement in project['dependencies']:
        # "pygame-ce>=2.5.8" and "x>=1; sys_platform == 'darwin'" both name
        # themselves before the first version specifier or marker.
        head = re.split(r'[<>=!~;\s\[]', requirement, maxsplit=1)[0]
        names.add(head.lower().replace('_', '-'))
    return names


def test_everything_the_game_imports_is_declared_in_the_manifest():
    """Undeclared, the game cannot start - in a checkout or in the bundle.

    Two real omissions lived here, and both are the kind that reads as a broken
    port rather than as a missing dependency:

      - engine.py emulates data/SoulTrapper.arm through unicorn. Nothing about
        that is optional, and it is imported *by* game.py rather than by the
        entry point, so a manifest written by reading only game.py misses it.
      - speech.py imports comtypes for the SAPI fallback, a level down again.

    Either way the symptom is a ModuleNotFoundError on the very first frame, in
    a fresh environment or in the bundle the player opens.
    """
    declared = declared_distributions()
    missing = {}
    for name, importers in third_party_imports().items():
        if distribution_of(name).lower() not in declared:
            missing[name] = sorted(importers)
    assert not missing, (
        'imported but not in pyproject.toml: '
        + ', '.join(f'{n} (from {", ".join(v)})' for n, v in missing.items()))


def test_the_manifest_does_not_declare_a_dependency_nothing_imports():
    """The other direction: a stale entry installs things nobody needs."""
    imported = {distribution_of(n) for n in third_party_imports()}
    orphans = sorted(declared_distributions() - imported)
    assert not orphans, f'declared but never imported: {orphans}'


def test_unicorn_is_a_dependency_and_not_a_footnote():
    """The game emulates the original ARM code; it is not optional."""
    assert 'unicorn' in third_party_imports(), \
        'engine.py should still be emulating SoulTrapper.arm'
    assert 'unicorn' in (Path(ROOT) / 'pyproject.toml').read_text(encoding='utf8')


def test_each_build_layer_checks_what_it_actually_owns():
    """The runtime checks dependencies; the bundle checks the game.

    Getting this backwards is easy and produces a check that cannot pass: the
    runtime is built in a scratch folder before any game file exists, so it
    cannot import engine, and asserting that it can stops every build.
    """
    runtime = (Path(ROOT) / 'tools' / 'build_mac_runtime.sh').read_text(
        encoding='utf8')
    import tools.build_mac_app as build
    body = code_of(build.check_bundle_imports)

    # The runtime: dependencies only.
    assert 'from unicorn import' in runtime
    assert 'import engine' not in runtime, \
        'no game file exists yet where the runtime is built'
    # The bundle: the game, with the runtime it carries.
    assert 'import engine' in body or '_internal' in body
    assert 'check_bundle_imports' in inspect_source(build.build)
    # And it must run against the carried interpreter, not this one.
    assert 'bin' in body and 'python3' in body


def test_the_launcher_actually_starts_the_game_from_inside_the_bundle():
    """Launcher.cs sets the working directory to the game root; so must this.

    Run against a stand-in for game.py, because the real one needs the game's
    audio, which is not in a fresh clone.  What is being checked is the
    launcher's own contract: the right interpreter, the right script, the
    --launcher flag, the working directory, and the exit code passed back out.
    """
    import subprocess
    import tempfile
    import tools.build_mac_app as build
    if not host.MAC:
        return
    source = Path(tempfile.mkdtemp())
    (source / '_internal').mkdir()
    for name in ('game.py', 'host.py'):
        (source / '_internal' / name).write_text('')
    for name in ('assets', 'data', 'vendor'):
        (source / name).mkdir()
    (source / '_internal' / 'game.py').write_text(
        'import os, sys\n'
        'assert "--launcher" in sys.argv, sys.argv\n'
        'assert os.path.basename(os.getcwd()) == "Soul Trapper", os.getcwd()\n'
        'open("ran-here.txt", "w").close()\n')
    out = Path(tempfile.mkdtemp())
    bundle = Path(build.build(str(source), 'sys', str(out), portable=False))
    launcher = bundle / 'Contents' / 'MacOS' / build.APP_NAME
    assert os.access(launcher, os.X_OK), 'the launcher must be executable'
    done = subprocess.run([str(launcher)], capture_output=True, text=True, cwd='/')
    assert done.returncode == 0, done.stdout + done.stderr
    resources = bundle / 'Contents' / 'Resources' / 'Soul Trapper'
    assert (resources / 'ran-here.txt').is_file(), 'the game never ran'
    assert not (resources / 'error.log').exists(), \
        'a successful run must not report a crash'


# ------------------------------------------------- the Windows build, on a Mac
# The point of the port is that Windows keeps working, and most of what could
# break is a one-word change in a path or an environment variable that nobody
# on a Mac ever exercises.  So the Windows branches are decided here by
# flipping host's own flags, and checked on this machine.


@contextmanager
def pretending(**flags):
    """Temporarily decide that we are on some other platform."""
    saved = {k: getattr(host, k) for k in flags}
    try:
        for k, v in flags.items():
            setattr(host, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(host, k, v)


def test_windows_still_finds_its_openal_dll_where_the_zip_keeps_it():
    with pretending(WINDOWS=True, MAC=False):
        assert host.openal_library_name() == 'vendor/openal/soft_oal.dll'
        assert host.openal_bare_name() == 'soft_oal.dll'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'vendor' / 'openal').mkdir(parents=True)
            (root / 'vendor' / 'openal' / 'soft_oal.dll').write_bytes(b'')
            assert host.openal_library(root) == \
                root / 'vendor' / 'openal' / 'soft_oal.dll'


def test_windows_still_keeps_saves_beside_the_game_in_the_zip():
    with pretending(WINDOWS=True, MAC=False):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / 'Soul Trapper'
            internal = game / '_internal'
            internal.mkdir(parents=True)
            assert host.is_portable(internal), \
                'the _internal folder is how the Windows ZIP is recognised'
            (game / 'portable.txt').write_text('')
            assert host.is_portable(internal)


def test_windows_still_keeps_saves_in_localappdata():
    with pretending(WINDOWS=True, MAC=False):
        with tempfile.TemporaryDirectory() as tmp:
            saved = os.environ.get('LOCALAPPDATA')
            os.environ['LOCALAPPDATA'] = tmp
            try:
                d = host.user_data_dir()
                assert d == Path(tmp) / 'SoulTrapperWindows', d
            finally:
                if saved is None:
                    os.environ.pop('LOCALAPPDATA', None)
                else:
                    os.environ['LOCALAPPDATA'] = saved


def test_windows_still_asks_for_nvda_then_sapi():
    with pretending(WINDOWS=True, MAC=False):
        assert host.speech_backend_order() == ['nvda', 'sapi', 'null']
        assert host.quit_hint() == 'Alt+F4'


def test_the_two_platforms_do_not_share_a_constant():
    """Both directions: one shared value would quietly break whichever is wrong."""
    with pretending(WINDOWS=True, MAC=False):
        windows_library = host.openal_library_name()
        windows_speech = host.speech_backend_order()
        windows_data = host.user_data_dir()
    with pretending(WINDOWS=False, MAC=True):
        assert host.openal_library_name() != windows_library
        assert host.openal_library_name().endswith('libopenal.dylib')
        assert host.speech_backend_order() != windows_speech
        assert host.speech_backend_order() == ['voiceover', 'null']
        assert host.user_data_dir() != windows_data


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f'  PASS  {fn.__name__}')
        except Exception as e:                                       # noqa: BLE001
            failed += 1
            print(f'  FAIL  {fn.__name__}: {e}')
    print(f'\n{len(fns) - failed}/{len(fns)} passed')
    raise SystemExit(1 if failed else 0)