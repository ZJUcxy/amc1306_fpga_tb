# -*- coding: utf-8 -*-
"""
Golden model of the 51-tap FIR (rtl/fir_lpf.v) plus a before/after preview of
the whole chain (AMC1306 modulator -> sinc3 -> FIR).

Two jobs:

  1. verify the integer datapath against the MATLAB design:
       - impulse response must equal (20000*coef) >> 15
       - |H(f)| at the test points must match fir_spec.vh
     This is the same arithmetic the Verilog does, so if it matches here the
     RTL datapath is right and any mismatch in xsim is a wiring problem.

  2. render ../fir/preview_pre_post.png : what the samples look like before
     and after the filter, for a step and for a 4 kHz tone.

Usage:  python golden_fir.py [--plot]
"""

import argparse
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.normpath(os.path.join(HERE, ".."))
COE = os.path.join(SIM, "fir", "amc1306_lpf_51tap.coe")
SPEC = os.path.join(SIM, "data", "fir_spec.vh")
PNG = os.path.join(SIM, "fir", "preview_pre_post.png")

FS = 62500.0
OSR = 256
SHIFT = 15          # FIR: CIC-free path, 16x16 MAC scaled by 2^15
CIC_SHIFT = 8       # AMC1306 chain: CIC gain 2^24 -> 16-bit needs >>8


# ------------------------------------------------------------------ helpers
def load_coef(path=COE):
    txt = open(path, encoding="ascii").read().split("coefdata=")[1]
    return [int(m) for m in re.findall(r"(-?\d+)\s*[,;]", txt)]


def load_spec(path=SPEC):
    out = {}
    for line in open(path, encoding="ascii"):
        m = re.match(r"localparam real (\w+)\s*=\s*([-\d.eE+]+);", line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def sat16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def fir_stream(x, coef):
    """Bit-exact twin of fir_lpf.v: full-precision MAC, >>>15, saturate."""
    n = len(coef)
    hist = [0] * n
    out = []
    for v in x:
        # RTL taps: din == hist[0] after the shift, sr[k-1] == hist[k]
        acc = v * coef[0] + sum(hist[k - 1] * coef[k] for k in range(1, n))
        out.append(sat16(acc >> SHIFT))
        hist = [v] + hist[:-1]
    return out


def cic_stream(bits, osr=OSR):
    """sinc3 CIC, same fixed-point as amc1306_sample_stub.v."""
    mask = 0xFFFFFFFF

    def w32(v):
        v &= mask
        return v - 0x100000000 if v & 0x80000000 else v

    i1 = i2 = i3 = 0
    z1 = z2 = z3 = 0
    dec = 0
    out = []
    for b in bits:
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
            out.append(sat16(w32((d3 >> CIC_SHIFT) - 32768)))
        else:
            dec += 1
    return out


# ------------------------------------------------------- verification tests
def verify(coef):
    print(f"coefficients : {len(coef)} taps, sum = {sum(coef)} "
          f"({'OK, DC gain 1.0' if sum(coef) == 32768 else 'WRONG'})")

    # 1. impulse response against the coefficient table
    imp = [20000] + [0] * (len(coef) + 10)
    y = fir_stream(imp, coef)
    worst = max(abs(y[k] - ((20000 * coef[k]) >> SHIFT)) for k in range(len(coef)))
    print(f"impulse resp : worst |err| = {worst} LSB vs (20000*coef)>>15")

    # 2. DC gain
    dc = fir_stream([10000] * 200, coef)
    print(f"DC           : in 10000 -> out {dc[-1]} (err {dc[-1]-10000} LSB)")

    # 3. frequency response at the MATLAB test points
    spec = load_spec()
    tests = [("FIR_GAIN_1K0", 1000.0), ("FIR_GAIN_2K5", 2500.0),
             ("FIR_GAIN_4K0", 4000.0), ("FIR_GAIN_12K0", 12000.0)]
    amp = 20000
    print(f"{'f [Hz]':>8} {'out amp':>8} {'gain':>8} {'MATLAB':>8} {'err':>7} {'dB':>7}")
    worst_g = 0.0
    for key, f in tests:
        n = 512
        x = [int(round(amp * np.sin(2 * np.pi * (f / FS) * k))) for k in range(n)]
        y = fir_stream(x, coef)
        tail = np.array(y[256:], dtype=float)
        a = (tail.max() - tail.min()) / 2.0
        g = a / amp
        ge = spec[key]
        worst_g = max(worst_g, abs(g - ge) / max(ge, 1e-9))
        db = 20 * np.log10(g) if g > 0 else -99
        print(f"{f:>8.0f} {a:>8.1f} {g:>8.4f} {ge:>8.4f} "
              f"{100*(g-ge)/max(ge,1e-9):>6.1f}% {db:>7.2f}")
    print(f"worst gain error vs MATLAB: {100*worst_g:.1f} %")
    return worst_g < 0.05 and worst == 0


# ------------------------------------------------------------- chain preview
def build_chain(vin_samples, v_clip=0.320, seed=7):
    import random
    sys.path.insert(0, HERE)
    from gen_bitstream import make_stream
    rng = random.Random(seed)          # same RNG interface as the generator
    state = [0.0, 0.0]
    bits = bytearray()
    for v in vin_samples:
        bits += make_stream(float(v), OSR, rng, v_clip, 4.5e-6, 12e-6, 10e-6, state)
    return cic_stream(bits)[4:]        # drop the CIC priming samples


def plot(coef):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(2, 1, figsize=(11, 7.5))

    # ---- panel A: step 0 -> +250 mV ------------------------------------
    vin = [0.0] * 40 + [0.250] * 200
    pre = build_chain(vin)
    post = fir_stream(pre, coef)
    t = np.arange(len(pre)) / FS * 1e3
    ax[0].plot(t, pre, lw=0.9, color="0.55", label="before (AMC1306 + sinc3)")
    ax[0].plot(t, post, lw=1.6, color="#d62728", label="after (51-tap FIR)")
    ax[0].set_title("step 0 -> +250 mV (full scale): the FIR smooths it over "
                    "51 samples = 0.82 ms")
    ax[0].set_ylabel("code [LSB]")
    ax[0].grid(alpha=0.3)
    ax[0].axvline((40 - 4) / FS * 1e3, color="k", ls=":", lw=0.8)

    # ---- panel B: 4 kHz tone -------------------------------------------
    n = 400
    vin = [0.250 * np.sin(2 * np.pi * 4000.0 * k / FS) for k in range(n)]
    pre = build_chain(vin)
    post = fir_stream(pre, coef)
    t = np.arange(len(pre)) / FS * 1e3
    ax[1].plot(t, pre, lw=0.9, color="0.55", label="before")
    ax[1].plot(t, post, lw=1.6, color="#1f77b4", label="after")
    a_pre = (max(pre[200:]) - min(pre[200:])) / 2
    a_post = (max(post[200:]) - min(post[200:])) / 2
    att = 20 * np.log10(a_post / a_pre) if a_pre else 0
    ax[1].set_title(f"4 kHz tone: {a_pre:.0f} LSB before -> {a_post:.0f} LSB after "
                    f"({att:+.1f} dB)")
    ax[1].set_xlabel("time [ms]  (Fs = 62.5 kSPS)")
    ax[1].set_ylabel("code [LSB]")
    ax[1].grid(alpha=0.3)

    for a in ax:
        a.legend(loc="lower right", fontsize=9)
        a.set_xlim(0, t[-1])

    fig.suptitle("AMC1306 chain: samples before and after the 51-tap low-pass FIR",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(PNG, dpi=140)
    print(f"wrote        : {PNG}")
    print(f"4 kHz measured: {a_pre:.0f} -> {a_post:.0f} LSB ({att:+.1f} dB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true", help="render the before/after png")
    a = ap.parse_args()

    coef = load_coef()
    ok = verify(coef)
    if a.plot:
        plot(coef)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
