"""Screen-reader output. VoiceOver on the Mac, NVDA then SAPI on Windows.

The backend is chosen by the platform, never by what happens to be importable:
host.speech_backend_order() is the whole decision, and an unusable backend is
skipped rather than being half-used.  Every backend replaces what it was
saying, because the game speaks over itself constantly - a menu item, then a
volume reading, then the scene - and a screen reader that queues them just
falls minutes behind.
"""
import ctypes
from pathlib import Path
from host import speech_backend_order

class NullSpeech:
    """Records nothing and says nothing: the game still runs, silently."""
    name = 'null'
    def say(self, text): pass
    def stop(self): pass

class NvdaSpeech:
    """Direct NVDA Controller Client, no COM, lowest latency."""
    name = 'nvda'
    def __init__(self, root):
        self.nvda = ctypes.WinDLL(str(Path(root) / 'vendor' / 'nvdaControllerClient64.dll'))
        for name in ('speakText', 'brailleMessage'):
            fn = getattr(self.nvda, 'nvdaController_' + name)
            fn.argtypes = [ctypes.c_wchar_p]
            fn.restype = ctypes.c_ulong
        for name in ('testIfRunning', 'cancelSpeech'):
            fn = getattr(self.nvda, 'nvdaController_' + name)
            fn.argtypes = []
            fn.restype = ctypes.c_ulong
        # The DLL loads whether or not NVDA is running, so this is the only thing
        # that tells the two apart. Without it an installed-but-stopped NVDA
        # would be chosen as the backend and the game would say nothing at all,
        # where it used to fall through to SAPI.
        if self.nvda.nvdaController_testIfRunning() != 0:
            raise OSError('NVDA is not running')
    def say(self, text):
        # Re-checked every time: NVDA can be closed while the game is open.
        if self.nvda.nvdaController_testIfRunning() == 0:
            self.nvda.nvdaController_cancelSpeech()
            if self.nvda.nvdaController_speakText(text) == 0:
                self.nvda.nvdaController_brailleMessage(text)
    def stop(self):
        if self.nvda.nvdaController_testIfRunning() == 0:
            self.nvda.nvdaController_cancelSpeech()

class SapiSpeech:
    """Windows SAPI 5 fallback, preferring a 409 (English UK) voice when it has one."""
    name = 'sapi'
    def __init__(self, root):
        import comtypes.client
        self.voice = comtypes.client.CreateObject('SAPI.SpVoice')
        voices = self.voice.GetVoices()
        for index in range(voices.Count):
            voice = voices.Item(index)
            if '409' in voice.GetAttribute('Language').split(';'):
                self.voice.Voice = voice
                break
    def say(self, text): self.voice.Speak(text, 3)  # 3 = asynchronous, purge first
    def stop(self): self.voice.Speak('', 3)

class VoiceOverSpeech:
    """VoiceOver, which is what the Mac has instead of NVDA and SAPI.

    The work is in voiceover.py, a port of the accessibility mod's
    VoiceOverOutput.cs: an Apple Event to VoiceOver, falling back to an
    accessibility announcement when the Event is refused. braille() is not a
    separate route - VoiceOver renders what it speaks onto a braille display
    itself - so say() covers it.
    """
    name = 'voiceover'
    def __init__(self, root):
        import voiceover
        if not voiceover.is_supported(): raise OSError('VoiceOver output is unavailable')
        self.vo = voiceover
    def say(self, text): self.vo.speak(text)
    def stop(self): self.vo.cancel()

BACKENDS = {'voiceover': VoiceOverSpeech, 'nvda': NvdaSpeech, 'sapi': SapiSpeech, 'null': NullSpeech}

class Speech:
    def __init__(self, root, silent=False):
        self.last = ''
        self.silent = silent
        self.backend = NullSpeech()
        if silent: return
        for kind in speech_backend_order():
            try:
                self.backend = BACKENDS[kind](root); break
            except Exception:
                continue

    def say(self, text):
        self.last = text
        if not self.silent: self.backend.say(text)

    def stop(self):
        if not self.silent: self.backend.stop()
