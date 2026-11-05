# -*- coding: utf-8 -*-
"""
Filter delay vs. loop stability: what really costs the 0.40 ms?

Designs a handful of low-pass filters that differ in *transition width* (and
therefore in group delay), measures each one's delay and attenuation, then runs
the 5 kHz current loop with each of them at the same PI bandwidth.

    python filter_tradeoff.py

The point it makes: group delay of a causal filter with a given magnitude spec
is set by the transition width, not by the filter type.  Replacing the
linear-phase FIR with an elliptic IIR of the same sharpness does *not* buy
delay -- widening the transition band does.
"""

import os
import sys

import numpy as np
from scipy import signal as sg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import amc1306_loop as L  # noqa: E402

FS = L.FS_ADC
FP = 1000.0
FST = 2500.0
FC_LOOP = 800.0            # PI bandwidth, the same for every candidate


def make_fir(n_taps, f_stop, att_target=40.0):
    """Equiripple low-pass, 1 kHz pass band, stop band edge f_stop."""
    w = 2.0 if f_stop < 5 * FP else 1.0
    return sg.remez(n_taps - 1, [0, FP, f_stop, FS / 2], [1, 0],
                    weight=[1, w], fs=FS)


def spec(b, a):
    """Group delay at DC / 1 kHz and attenuation at the stop edge and 4 kHz."""
    ww, g = sg.group_delay((b, a), w=[0.0, FP], fs=FS)
    wv, hv = sg.freqz(b, a, worN=1 << 15, fs=FS)
    m = 20 * np.log10(np.abs(hv) + 1e-15)
    return (g[0] / FS * 1e6, g[1] / FS * 1e6,
            m[np.argmin(abs(wv - FST))], m[np.argmin(abs(wv - 4000))])


def quantise(h, bits=16, scale=2 ** 15):
    q = np.round(h / np.sum(h) * scale).astype(int)
    q[len(q) // 2] += int(scale - q.sum())
    return [int(v) for v in q]


def run_case(name, coef):
    r = L.simulate(use_fir=True, fc_hz=FC_LOOP, use_bits=True, seed=2, coef=coef)
    m = L.metrics(r)
    return m


def main():
    print(f"current loop: PI {FC_LOOP:.0f} Hz, 25 kHz PWM, AMC1306 + sinc3")
    print()
    print(f"{'filter':<28}{'taps':>5}{'GD@DC':>9}{'GD@1k':>8}{'att@2.5k':>10}"
          f"{'att@4k':>9}{'over%':>8}{'settle':>9}{'ripple':>8}")
    print("-" * 96)

    rows = []
    # (label, taps, stop-edge for the design)
    cand = [
        ("no extra filter (sinc3)", None, None),
        ("FIR 51  (1k/2.5k, now)", 51, 2500.0),
        ("FIR 17  (1k/10k)", 17, 10000.0),
        ("FIR 9   (1k/20k)", 9, 20000.0),
        ("FIR 5   (1k/30k)", 5, 30000.0),
    ]
    for label, n, fst in cand:
        if n is None:
            coef = [0] * 1
            r = L.simulate(use_fir=False, fc_hz=FC_LOOP, use_bits=True, seed=2)
            m = L.metrics(r)
            print(f"{label:<28}{'-':>5}{'-':>9}{'-':>8}{'-':>10}{'-':>9}"
                  f"{m['overshoot_pct']:>7.1f}%{m['settle_ms']:>8.1f}ms{m['ripple_A']:>7.3f}")
            continue
        h = make_fir(n, fst)
        gd0, gd1, a25, a4 = spec(h, [1])
        coef = quantise(h)
        m = run_case(label, coef)
        print(f"{label:<28}{n:>5}{gd0:>8.0f}us{gd1:>7.0f}us{a25:>9.1f}dB{a4:>8.1f}dB"
              f"{m['overshoot_pct']:>7.1f}%{m['settle_ms']:>8.1f}ms{m['ripple_A']:>7.3f}")
        rows.append((label, n, gd0))

    # same sharpness, different filter type
    print()
    print("same 1 kHz / 2.5 kHz spec, other filter types (replaces the 51-tap FIR):")
    for name, ftype in (("elliptic 3rd", "ellip"), ("cheby2 5th", "cheby2")):
        b, a = sg.iirdesign(FP, FST, 1, 40, ftype=ftype, fs=FS)
        gd0, gd1, a25, a4 = spec(b, a)
        print(f"{name:<28}{len(b)-1:>5}{gd0:>8.0f}us{gd1:>7.0f}us{a25:>9.1f}dB{a4:>8.1f}dB"
              f"{'  (IIR: same delay, no win)':>28}")


if __name__ == "__main__":
    main()
