"""Conservative PCM headroom adjustment for quiet original recordings.

All of this is 16-bit PCM, which is all the game has; the width is a parameter
only because ``audioop`` insists on one.
"""
import math
import sys
from array import array
from pathlib import Path

try:
    # The fast path, and what the Windows build has always used. audioop was
    # removed in Python 3.13, so this import can fail; the fallbacks below are
    # transcriptions of its C (Modules/audioop.c) and produce the same bytes.
    import audioop
except ImportError:
    audioop = None

#: audioop's minvals[2] and maxvals[2].
MIN_16 = -0x8000
MAX_16 = 0x7FFF

def _samples(pcm):
    a = array('h')
    a.frombytes(pcm)
    if sys.byteorder == 'big':  # pragma: no cover - audioop's data is always LE
        a.byteswap()
    return a

def _max(pcm, width):
    if audioop: return audioop.max(pcm, width)
    # The largest *absolute* sample, not the largest one: audioop casts to
    # unsigned before negating, so an all-negative passage peaks at its quietest
    # end rather than reporting a negative peak and never being boosted.
    return max((abs(s) for s in _samples(pcm)), default=0)

def _rms(pcm, width):
    if audioop: return audioop.rms(pcm, width)
    a = _samples(pcm)
    if not a: return 0
    # sum(S_i^2)/n then the square root, truncated - no rounding at the end.
    # The running total is a plain double accumulation in sample order, as in C.
    total = 0.
    for s in a: total += s * s
    return int(math.sqrt(total / len(a)))

def _mul(pcm, width, factor):
    if audioop: return audioop.mul(pcm, width, factor)
    if factor == 1: return pcm
    a = _samples(pcm)
    # Clamp, then floor. Flooring is not truncation toward zero: for a negative
    # sample, -101 * 1.6 is -161.6, which floors to -162 and truncates to -161.
    for i, s in enumerate(a):
        v = s * factor
        if v > MAX_16: v = MAX_16
        elif v < MIN_16 + 1.: v = MIN_16
        a[i] = math.floor(v)
    if sys.byteorder == 'big': a.byteswap()  # pragma: no cover
    return a.tobytes()

def enhance(name, pcm):
    stem=Path(name).stem
    if stem in {f'C19_FX_{i:03}' for i in range(8)}:
        peak=_max(pcm,2)
        return _mul(pcm,2,min(1.6,32000/max(1,peak)))
    if '_BG_' in stem:
        # Fixed per-recording gain preserves dynamics, stereo and authored fades.
        # Leave louder ambiences (including the apartment) at their original level.
        rms=_rms(pcm,2);peak=_max(pcm,2)
        gain=max(1.,min(4.,400/max(1,rms),30000/max(1,peak)))
        if gain>1.:return _mul(pcm,2,gain)
    return pcm
