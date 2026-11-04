# -*- coding: utf-8 -*-
"""
Golden model of the reference RTL (rtl/amc1306_sample_stub.v).

This is a *bit-exact* python twin of the Verilog, including the intentional
32-bit wraparound of the CIC integrators and comb delays.  It exists so the
fixed-point arithmetic can be verified without a Verilog simulator: if this
model reproduces the ideal codes, the RTL datapath does too.

Usage:  python golden_cic.py [--mem ../data/scenario.mem] [--part m25]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_bitstream import PARTS, ideal_code, clip_pattern  # noqa: E402

MASK = 0xFFFFFFFF


def w32(x):
    """Wrap to a 32-bit two's complement value, like a Verilog reg [31:0]."""
    x &= MASK
    return x - 0x100000000 if x & 0x80000000 else x


def decode(bits, osr=256, div=16, shift=8, offset=32768):
    """Stream `bits` (one per CLKIN) through the RTL datapath, return codes."""
    i1 = i2 = i3 = 0
    z1 = z2 = z3 = 0
    dec = 0
    out = []
    for b in bits:
        # CLKIN period boundary: integrators take the bit sampled at CLKIN fall
        i1n = w32(i1 + b)
        i2n = w32(i2 + i1 + b)
        i3n = w32(i3 + i2 + i1 + b)
        d1 = w32(i3n - z1)
        d2 = w32(d1 - z2)
        d3 = w32(d2 - z3)
        i1, i2, i3 = i1n, i2n, i3n
        if dec == osr - 1:
            z1, z2, z3 = i3n, d1, d2
            dec = 0
            s = w32((d3 >> shift) - offset)       # >>> 8 then -32768
            s = 32767 if s > 32767 else (-32768 if s < -32768 else s)
            out.append(s)
        else:
            dec += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mem", default=os.path.join(os.path.dirname(__file__),
                                                  "..", "data", "scenario.mem"))
    ap.add_argument("--part", default="m25")
    ap.add_argument("--seg-bits", type=int, default=2048)
    ap.add_argument("--osr", type=int, default=256)
    ap.add_argument("--settle", type=int, default=4)
    a = ap.parse_args()

    bits = [1 if line.strip() == "1" else 0 for line in open(a.mem, encoding="ascii")]
    v_clip, v_fs = PARTS[a.part]
    codes = decode(bits, osr=a.osr)

    segs = []
    for k in range(9):
        segs.append((f"{-1.0 + 0.25*k:+.2f} FS", (-1.0 + 0.25*k) * v_fs, 0))
    segs.append(("+clip", v_clip, 2))
    segs.append(("-clip", -v_clip, 2))
    segs.append(("0 + 100uV", 100e-6, 0))

    print(f"{'seg':>3} {'name':<10} {'ideal':>7} {'first':>7} {'last':>7} "
          f"{'mean':>7} {'max|err|':>8}")
    print("-" * 60)
    worst = 0
    for idx, (name, vin, kind) in enumerate(segs):
        chunk = codes[idx * a.seg_bits // a.osr:(idx + 1) * a.seg_bits // a.osr]
        chunk = chunk[a.settle:]                    # skip the settling windows
        exp = ideal_code(vin, v_clip)
        if kind == 2:
            errs = [0 if abs(c) >= 24000 else 1 for c in chunk]
            e = max(errs) if errs else 0
        else:
            e = max(abs(c - exp) for c in chunk) if chunk else 0
            worst = max(worst, e)
        mean = round(sum(chunk) / len(chunk))
        print(f"{idx:>3} {name:<10} {exp:>7} {chunk[0]:>7} {chunk[-1]:>7} "
              f"{mean:>7} {e:>8}")
    print("-" * 60)
    print(f"worst linear error (32-bit RTL arithmetic): {worst} LSB")
    return 0 if worst <= 8 else 1


if __name__ == "__main__":
    sys.exit(main())
