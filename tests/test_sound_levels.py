"""The pure-Python PCM fallbacks must be byte-identical to ``audioop``.

``audioop`` was removed in Python 3.13, so ``sound_levels.py`` carries its own
version of ``max``, ``rms`` and ``mul`` for the Mac.  It is only ever used when
``audioop`` is missing - the Windows build still runs the C - which is exactly
why the two cannot be allowed to drift: the same recording would be levelled
differently depending on the interpreter it was played through, and a mismatch
in ``max`` or ``rms`` does not fail loudly, it just boosts by the wrong amount.

So these are characterisation tests against ``reference()`` below, which is a
line-by-line transcription of ``Modules/audioop.c`` and is deliberately *not*
the shape ``sound_levels.py`` uses: same arithmetic, different code.

Where ``audioop`` is installed they also run directly against it, which is the
stronger check of the two.

    python tests/test_sound_levels.py
"""

import math
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sound_levels                                        # noqa: E402

try:
    import audioop
except ImportError:
    audioop = None

MAXVAL = 0x7FFF
MINVAL = -0x8000


# --------------------------------------------------------------- reference (C)
def fbound(val):
    """audioop.c's fbound(), with width 2's minval and maxval."""
    if val > MAXVAL:
        val = MAXVAL
    elif val < MINVAL + 1.0:
        val = MINVAL
    val = math.floor(val)
    return int(val)


def reference(name, pcm):
    """sound_levels.enhance() as Modules/audioop.c computes it."""
    samples = struct.unpack('<%dh' % (len(pcm) // 2), pcm)

    def amax():
        # C casts to unsigned before negating, so the accumulator is unsigned
        # and 0 is the floor.
        best = 0
        for s in samples:
            a = -s if s < 0 else s
            if a > best: best = a
        return best

    def arms():
        if not samples: return 0
        total = 0.0
        for s in samples:
            val = float(s)
            total += val * val
        return int(math.sqrt(total / float(len(samples))))

    def amul(factor):
        return struct.pack('<%dh' % len(samples),
                           *[fbound(float(s) * factor) for s in samples])

    stem = os.path.splitext(name)[0].split('/')[-1].split('\\')[-1]
    for i in range(8):
        if stem == f'C19_FX_{i:03}':
            return amul(min(1.6, 32000 / max(1, amax())))
    if '_BG_' in stem:
        gain = max(1., min(4., 400 / max(1, arms()), 30000 / max(1, amax())))
        if gain > 1.: return amul(gain)
    return pcm


# --------------------------------------------------------------------- input
def samples():
    """PCM that hits the interesting cases: silence, full scale, and quiet."""
    yield b''
    yield b'\x00\x00'
    yield struct.pack('<2h', 0, 0)
    yield struct.pack('<4h', -32768, -32768, -1, -1)       # all negative
    yield struct.pack('<4h', 32767, 32767, 1, 1)           # all positive
    yield struct.pack('<4h', -32768, 32767, 0, -16384)
    yield struct.pack('<6h', -101, -103, 101, 103, -7, 7)  # negative * 1.6
    rng = random.Random(20240607)
    for n in (1, 2, 3, 63, 1024, 5000):
        for _ in range(4):
            yield struct.pack('<%dh' % n,
                              *[rng.randrange(-32768, 32768) for _ in range(n)])
    # A passage that is quiet enough to be boosted, which is the only reason
    # any of this exists.
    yield struct.pack('<8h', *([-200, -180, -220, -190] * 2))
    yield struct.pack('<8h', *[1200, -900, 1500, -1100] * 2)


NAMES = ('C19_FX_003.caf', 'C19_FX_000.ogg', 'C19_FX_007.wav',
         'amb_BG_kitchen.wav', 'a_BG_ambience.caf', 'plain_sfx.wav',
         'C19_FX.caf')


def without_audioop(fn, *args):
    """Call ``fn`` with sound_levels.audioop forced off."""
    saved = sound_levels.audioop
    sound_levels.audioop = None
    try:
        return fn(*args)
    finally:
        sound_levels.audioop = saved


# -------------------------------------------------------------------- tests
def test_the_pure_python_path_matches_audioop_source():
    for name in NAMES:
        for pcm in samples():
            got = without_audioop(sound_levels.enhance, name, pcm)
            want = reference(name, pcm)
            assert got == want, (name, len(pcm), got[:24], want[:24])


def test_it_matches_audioop_itself_where_that_is_installed():
    if audioop is None:
        return
    for name in NAMES:
        for pcm in samples():
            want = sound_levels.enhance(name, pcm)     # audioop is in use here
            got = without_audioop(sound_levels.enhance, name, pcm)
            assert got == want, (name, len(pcm))


def test_max_is_the_largest_absolute_sample():
    """Not the largest sample: an all-negative passage has a positive peak."""
    quiet_negative = struct.pack('<4h', -30000, -20000, -32768, -1000)
    assert without_audioop(sound_levels._max, quiet_negative, 2) == 32768
    if audioop is not None:
        assert audioop.max(quiet_negative, 2) == 32768


def test_rms_truncates_rather_than_rounds():
    """audioop casts the square root to an int; it does not add a half."""
    pcm = struct.pack('<2h', 10000, 10000)               # exactly 10000.0
    assert without_audioop(sound_levels._rms, pcm, 2) == 10000
    # Just under a whole number: 9999.75, which rounds to 10000 and truncates
    # to 9999.
    pcm = struct.pack('<2h', 9999, 10000)
    truncated = without_audioop(sound_levels._rms, pcm, 2)
    rounded = int(math.sqrt((9999 ** 2 + 10000 ** 2) / 2) + 0.5)
    assert truncated == rounded - 1
    if audioop is not None:
        assert audioop.rms(pcm, 2) == truncated


def test_mul_floors_rather_than_truncating():
    """-161.6 is -162 after audioop's floor, and -161 after int()."""
    pcm = struct.pack('<1h', -101)
    assert without_audioop(sound_levels._mul, pcm, 2, 1.6) == \
        struct.pack('<1h', fbound(-101 * 1.6))
    assert without_audioop(sound_levels._mul, pcm, 2, 1.6) == \
        struct.pack('<1h', -162)


def test_mul_clips_at_full_scale():
    pcm = struct.pack('<2h', 32767, -32768)
    for factor in (1.6, 4., 400.):
        got = without_audioop(sound_levels._mul, pcm, 2, factor)
        assert got == pcm, factor
    pcm = struct.pack('<2h', 20000, -20000)
    got = without_audioop(sound_levels._mul, pcm, 2, 4.)
    assert got == struct.pack('<2h', 32767, -32768)


def test_empty_and_silent_pcm_do_not_raise():
    for pcm in (b'', b'\x00\x00', b'\x00\x00' * 99):
        for name in NAMES:
            without_audioop(sound_levels.enhance, name, pcm)
        without_audioop(sound_levels._max, pcm, 2)
        without_audioop(sound_levels._rms, pcm, 2)


def test_a_unity_factor_returns_the_input_unchanged():
    pcm = struct.pack('<4h', -101, -103, 101, 103)
    assert without_audioop(sound_levels._mul, pcm, 2, 1.) is pcm
    if audioop is not None:
        assert audioop.mul(pcm, 2, 1.) == pcm


def test_which_path_will_be_taken_is_reported_honestly():
    """The fallback is a compatibility shim, and it should be findable.

    ``audioop`` was removed in 3.13 but is installable again as the
    ``audioop-lts`` drop-in, and the game should use that when it is there
    rather than its own version. So this asserts the choice, not the version:
    on 3.13+ either answer is legitimate, on anything older only audioop is.
    """
    if audioop is None:
        assert sys.version_info >= (3, 13), \
            f'Python {sys.version.split()[0]} has no audioop?'
    print(f'      (audioop {"present" if sound_levels.audioop is not None else "absent, using the fallback"})')


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