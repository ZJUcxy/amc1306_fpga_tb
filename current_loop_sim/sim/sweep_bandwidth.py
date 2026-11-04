# -*- coding: utf-8 -*-
"""Sweep the PI bandwidth for the FIR-filtered sensor and report where it dies."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from amc1306_loop import simulate, metrics  # noqa: E402


def main():
    print(f"{'fc [Hz]':>8} {'rise':>7} {'over [%]':>9} {'settle':>8} {'ripple':>8} {'zc':>4}")
    print("-" * 52)
    for fc in (80, 100, 120, 150, 180, 200, 230, 260, 300, 400):
        r = simulate(use_fir=True, fc_hz=fc, use_bits=True, seed=2)
        m = metrics(r)
        print(f"{fc:>8} {m['rise_ms']:>6.2f}ms {m['overshoot_pct']:>8.1f}% "
              f"{m['settle_ms']:>7.2f}ms {m['ripple_A']:>7.3f}A {m['zc']:>4}")


if __name__ == "__main__":
    main()
