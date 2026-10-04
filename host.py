"""Which platform this port is running on, decided once.

Everything platform-specific - the OpenAL library, the screen reader, the
where-saves-live folder, the crash dialog - sits behind this module, so the
game never branches on ``sys.platform`` itself.  A new platform means touching
this file, not the engine.

Windows and Mac, one equivalent for one:

=================================  ==========================================
Windows                            Mac
=================================  ==========================================
``vendor/openal/soft_oal.dll``     ``vendor/openal-mac/libopenal.dylib``
                                   (the same OpenAL Soft, built for macOS)
``nvdaControllerClient64.dll``     VoiceOver through pyobjc (``voiceover.py``,
                                   after the ``VoiceOverOutput`` pattern)
SAPI 5 fallback                    (VoiceOver is part of macOS, always)
``%LOCALAPPDATA%\\SoulTrapperWindows``  ``~/Library/Application Support/Soul Trapper``
``MessageBoxW``                    an ``osascript`` alert
``Soul Trapper.exe`` in a ZIP      ``Play Soul Trapper.app``
``Alt+F4``                         ``Cmd+Q``
=================================  ==========================================
"""
import os
import subprocess
import sys
import warnings
from pathlib import Path

WINDOWS = sys.platform == 'win32'
MAC = sys.platform == 'darwin'
PLATFORM = 'mac' if MAC else 'windows' if WINDOWS else 'other'

if PLATFORM == 'other':  # pragma: no cover - neither platform is supported yet
 warnings.warn(f'Soul Trapper has not been ported to {sys.platform!r}; '
               'some features will quietly do nothing.', stacklevel=2)

#: The game's version, for the Mac bundle's Info.plist and for error reports.
#: game.py carries its own copy of the same string in the window caption.
try: VERSION = (Path(__file__).resolve().parent / 'VERSION').read_text(encoding='utf8').strip() or '1.0'
except OSError: VERSION = '1.0'

#: The vendor library OpenAL Soft is loaded from, per platform.
def openal_library_name():
    return 'vendor/openal-mac/libopenal.dylib' if MAC else 'vendor/openal/soft_oal.dll'


#: The same library as PyInstaller's ``--add-binary`` leaves it: flat in the
#: resource root rather than under ``vendor/``.  Checked second on both.
def openal_bare_name():
    return 'libopenal.dylib' if MAC else 'soft_oal.dll'


def in_mac_app_bundle():
    """True when the game is running from inside a ``.app`` bundle.

    Decided by looking at the path rather than at ``sys.frozen``: the Mac build
    runs a real interpreter over the real ``game.py``, so nothing is frozen and
    there is nothing for a frozen check to find.  ``Contents/Resources`` in the
    path is the thing that actually identifies the layout.

    Not a writability test.  A bundle may be perfectly writable - in
    ``~/Applications``, or when the player has deliberately asked for portable
    saves - and it may be entirely read-only, and only trying to write tells you
    which.  See :func:`is_portable` for the save location and
    ``crash_report.py`` for the log, both of which try and fall back.
    """
    parts = Path(__file__).resolve().parts
    return MAC and 'Contents' in parts and 'Resources' in parts


def quit_hint():
    """How the player leaves, which no in-game key does on either platform."""
    return 'Cmd+Q' if MAC else 'Alt+F4' if WINDOWS else 'window close'


def speech_backend_order():
    """Which screen-reader backends to try, best first."""
    return ['voiceover', 'null'] if MAC else ['nvda', 'sapi', 'null']


def user_data_dir():
    """Where the player's own files live: saves, settings, progress, logs.

    Windows keeps the exact behaviour it has always had.  The Mac uses
    Application Support, which is where Mac applications keep per-user state -
    and is the only safe answer there, because a ``.app`` can sit somewhere
    nothing inside it is writable (``/Applications``, or the read-only mount
    App Translocation puts a quarantined download on).
    """
    if MAC:
        return Path(os.environ.get('HOME') or Path.home()) / 'Library' / 'Application Support' / 'Soul Trapper'
    if WINDOWS:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'SoulTrapperWindows'
    return Path.home() / '.soultrapper'  # pragma: no cover


def is_portable(module_dir):
    """Whether this copy of the game keeps its saves beside itself.

    ``module_dir`` is the folder holding game.py - ``_internal`` in the Windows
    ZIP build - and ``portable.txt`` sits one level up, beside the executable,
    which is what the Windows build has always looked for.  Both are honoured
    on either platform, because the marker is an explicit request.

    The bare ``_internal`` folder only counts on Windows, where it is how the
    ZIP build is recognised.  On the Mac the same folder sits inside a bundle
    that is usually not writable, so inferring portability from it there would
    write saves into a directory that cannot hold them.
    """
    folder = Path(module_dir)
    if (folder.parent / 'portable.txt').is_file():
        return True
    return WINDOWS and folder.name == '_internal'


def openal_library(root):
    """The OpenAL Soft library to load: the platform's own build of it.

    ``root`` is the folder holding the game modules.  The vendor directory is
    checked first, which is where the Windows ZIP keeps it; the bare file at
    the root is the fallback for a layout that drops the library in flat.
    """
    root = Path(root)
    for candidate in (root / openal_library_name(), root / openal_bare_name()):
        if candidate.is_file():
            return candidate
    return root / openal_library_name()


def prepare_library_path(path):
    """Let the loader find the OpenAL library's own dependencies.

    A Windows idea is ``os.add_dll_directory``; on the Mac the equivalent is
    telling dyld where ``@rpath`` resolves, which for our dylib is its own
    directory.  Call before ``ctypes.CDLL``.
    """
    lib_dir = str(Path(path).resolve().parent)
    if WINDOWS and hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(lib_dir)
        except OSError:
            pass
    elif MAC:
        env = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
        if lib_dir not in env.split(':'):
            os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = f'{lib_dir}:{env}' if env else lib_dir


def alert(title, message):
    """A modal error dialog, or the console where there is no other way to say it."""
    if WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0)
        return
    if MAC:
        script = ('display alert "%s" message "%s" as critical'
                  % (title.replace('"', r'\"'), message.replace('"', r'\"')))
        try:
            subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, timeout=30)
            return
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            pass
    print(f'{title}: {message}')  # pragma: no cover