# -*- coding: utf-8 -*-
"""
AMC1306M25 bitstream generator  ->  scenario.mem + scenario_params.vh

Produces realistic 1-bit delta-sigma streams (one bit per CLKIN cycle) that the
testbench streams into the DUT at 16 MHz.

Model basis (TI SBAS734C, AMC1306M25):
  * ones density      d = 0.5 + VIN / (2 * VClipping),  VClipping = 320 mV
  * at +/-250 mV (specified FS)  d = 10.9375 % / 89.0625 %
    -> 16-bit centred code = (d - 0.5) * 65536 = -25600 / +25600
  * over-range (|VIN| >= 320 mV): 127 bits of the rail + 1 opposite bit
    (datasheet Fig.54 "Test Pattern"), NOT a clean 0/1 rail
  * 2nd-order single-loop modulator (the part is 2nd order)
  * input chopper at fCLKIN/32 modelled as a small additive ripple
  * thermal noise + offset error so the stream is not a perfect limit cycle

Everything is deterministic for a given --seed, so the .mem file is reproducible.
"""

import argparse
import math
import os
import random
import sys

# ---------------------------------------------------------------- device data
PARTS = {
    # name: (v_clip_V, v_fs_V, vcm_hint)
    "m25": (0.320, 0.250),
    "m05": (0.064, 0.050),
}


def ideal_density(vin, v_clip):
    """Ones density from the datasheet relation, clamped to the legal rail."""
    d = 0.5 + vin / (2.0 * v_clip)
    return min(max(d, 0.0), 1.0)


def ideal_code(vin, v_clip):
    """16-bit two's complement code the ideal sinc3+scale should return."""
    d = ideal_density(vin, v_clip)
    code = int(round((d - 0.5) * 65536.0))
    return max(min(code, 32767), -32768)


def sigma_delta_2nd(x_stream, rng, a=0.5, b=0.4):
    """2nd-order single-loop 1-bit modulator.  x in [-1, 1)."""
    v1 = 0.0
    v2 = 0.0
    out = bytearray()
    for x in x_stream:
        y = 1 if v2 >= 0.0 else 0
        fb = 1.0 if y else -1.0
        v1 += a * (x - fb)
        v2 += b * (v1 - fb)
        out.append(y)
    return out


def clip_pattern(positive, n):
    """Over-range behaviour: 127 rail bits + 1 opposite bit, per 128 bits."""
    rail = 1 if positive else 0
    other = 0 if positive else 1
    out = bytearray()
    for i in range(n):
        out.append(rail if (i % 128) != 127 else other)
    return out


def make_stream(vin, n, rng, v_clip, v_offset, sigma_noise, chop_amp, state):
    """Full behavioural chain for one DC operating point.

    state = [v1, v2] is carried across segments so the modulator keeps running,
    exactly like the part does on a real board (no artificial restart glitch).
    """
    if abs(vin) >= v_clip:
        return clip_pattern(vin > 0, n)

    chop_period = 32                      # chopper runs at fCLKIN/32
    v1, v2 = state[0], state[1]
    out = bytearray()
    for i in range(n):
        x = vin + v_offset + rng.gauss(0.0, sigma_noise)
        x += chop_amp * (1.0 if (i % chop_period) < (chop_period // 2) else -1.0)
        xn = x / v_clip                    # normalise to [-1, 1]
        y = 1 if v2 >= 0.0 else 0
        fb = 1.0 if y else -1.0
        v1 += 0.5 * (xn - fb)
        v2 += 0.4 * (v1 - fb)
        out.append(y)
    state[0] = v1
    state[1] = v2
    return out


def cic_sinc3(bits, R=256, shift=8, offset=32768):
    """Bit-exact python twin of the RTL sinc3 decimator (used for validation)."""
    i1 = i2 = i3 = 0
    prev = None
    c1p = c2p = 0
    res = []
    for n, b in enumerate(bits):
        i1 += b
        i2 += i1
        i3 += i2
        if (n + 1) % R == 0:
            if prev is None:
                prev = i3
                res.append(None)
                continue
            c1 = i3 - prev
            prev = i3
            c2 = c1 - c1p
            c1p = c1
            c3 = c2 - c2p
            c2p = c2
            res.append((c3 >> shift) - offset)
    return res


def build_segments(v_clip, v_fs):
    """Test plan: | name | vin(V) | kind |  kind 0=linear 2=over-range."""
    segs = []
    for k in range(9):                     # -FS .. +FS in 0.25 FS steps
        frac = -1.0 + 0.25 * k
        segs.append((f"{frac:+.2f} FS", frac * v_fs, 0))
    segs.append(("+clip 320mV", v_clip, 2))
    segs.append(("-clip 320mV", -v_clip, 2))
    segs.append(("0 + 100uV offset", 100e-6, 0))
    return segs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="m25", choices=sorted(PARTS))
    ap.add_argument("--seg-bits", type=int, default=2048,
                    help="bits per test segment (= 8 sinc3 windows at OSR 256)")
    ap.add_argument("--warm-bits", type=int, default=2048,
                    help="discarded modulator warm-up bits before segment 0")
    ap.add_argument("--seed", type=int, default=20221104)
    ap.add_argument("--noise-uv", type=float, default=12.0, help="input-referred noise, uV rms")
    ap.add_argument("--offset-uv", type=float, default=4.5, help="input offset error, uV")
    ap.add_argument("--chop-uv", type=float, default=10.0, help="chopper ripple, uV")
    ap.add_argument("--fs", type=float, default=16e6, help="CLKIN rate used for fCLKIN/32")
    ap.add_argument("--outdir", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    args = ap.parse_args()

    v_clip, v_fs = PARTS[args.part]
    rng = random.Random(args.seed)
    segs = build_segments(v_clip, v_fs)

    os.makedirs(args.outdir, exist_ok=True)
    nbits_total = args.seg_bits * len(segs)

    stream = bytearray()
    state = [0.0, 0.0]
    # warm up: the modulator starts at v1 = v2 = 0, so its first couple of
    # windows do not yet carry the right density (one window comes out with an
    # extra 1, i.e. ~126 LSB).  Run 8 windows at the first level and throw them
    # away so every bit that lands in the .mem is steady state.
    make_stream(segs[0][1], args.warm_bits, rng, v_clip,
                args.offset_uv * 1e-6, args.noise_uv * 1e-6,
                args.chop_uv * 1e-6, state)
    for name, vin, kind in segs:
        stream += make_stream(vin, args.seg_bits, rng, v_clip,
                              args.offset_uv * 1e-6, args.noise_uv * 1e-6,
                              args.chop_uv * 1e-6, state)

    # ------------------------------------------------------------------ write
    mem_path = os.path.join(args.outdir, "scenario.mem")
    with open(mem_path, "w", encoding="ascii", newline="\n") as f:
        for b in stream:
            f.write("1\n" if b else "0\n")

    # per-segment files, handy for MATLAB / Python analysis or a simpler TB
    seg_dir = os.path.join(args.outdir, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    for idx, (name, vin, kind) in enumerate(segs):
        chunk = stream[idx * args.seg_bits:(idx + 1) * args.seg_bits]
        safe = name.replace(" ", "").replace("/", "p").replace("+", "p").replace("-", "m")
        with open(os.path.join(seg_dir, f"seg{idx:02d}_{safe}.mem"), "w",
                  encoding="ascii", newline="\n") as f:
            for b in chunk:
                f.write("1\n" if b else "0\n")

    # ------------------------------------------------------------- validation
    rows = []
    for idx, (name, vin, kind) in enumerate(segs):
        chunk = stream[idx * args.seg_bits:(idx + 1) * args.seg_bits]
        exp = ideal_code(vin, v_clip)
        meas_density = sum(chunk) / float(len(chunk))
        codes = [c for c in cic_sinc3(chunk) if c is not None][3:]   # skip priming/transient
        if kind == 2:
            # over-range: only the extreme magnitude is meaningful
            errs = [0 if abs(c) >= 24000 else 1 for c in codes]
            max_err = max(errs) if errs else 0
            mean_code = int(sum(codes) / len(codes)) if codes else 0
        else:
            errs = [c - exp for c in codes]
            max_err = max(abs(e) for e in errs) if errs else 0
            mean_code = int(round(sum(codes) / float(len(codes)))) if codes else 0
        rows.append((idx, name, vin * 1e3, exp, meas_density * 100.0,
                     mean_code, max_err, kind))

    vh_path = os.path.join(args.outdir, "scenario_params.vh")
    with open(vh_path, "w", encoding="ascii", newline="\n") as f:
        f.write("// ===================================================================\n")
        f.write("// AUTO-GENERATED by gen_bitstream.py -- do not edit by hand.\n")
        f.write(f"// part={args.part}  VClipping={v_clip*1e3:.0f}mV  "
                f"FS={v_fs*1e3:.0f}mV  seed={args.seed}\n")
        f.write("// ===================================================================\n")
        f.write(f"localparam integer SEG_BITS  = {args.seg_bits};\n")
        f.write(f"localparam integer NUM_SEG   = {len(segs)};\n")
        f.write(f"localparam integer BIT_DEPTH = {nbits_total};\n")
        f.write(f"localparam real    V_CLIP_MV = {v_clip*1e3:.1f};\n")
        f.write(f"localparam real    V_FS_MV   = {v_fs*1e3:.1f};\n")

    tab_path = os.path.join(args.outdir, "scenario_table.vh")
    with open(tab_path, "w", encoding="ascii", newline="\n") as f:
        f.write("// AUTO-GENERATED by gen_bitstream.py -- include AFTER the arrays.\n")
        f.write("initial begin\n")
        for idx, name, vin_mv, exp, dens, mean_code, max_err, kind in rows:
            f.write(f"  seg_expected[{idx}] = {exp};  seg_kind[{idx}] = {kind};  "
                    f"seg_vin_mv[{idx}] = {vin_mv:.1f};  seg_name[{idx}] = \"{name:<20}\";\n")
        f.write("end\n")
    with open(os.path.join(args.outdir, "scenario_meta.csv"), "w",
              encoding="ascii", newline="\n") as f:
        f.write("seg,name,vin_mV,ideal_code,measured_density_pct,mean_code,max_abs_err_LSB,kind\n")
        for idx, name, vin_mv, exp, dens, mean_code, max_err, kind in rows:
            f.write(f"{idx},\"{name}\",{vin_mv:.1f},{exp},{dens:.4f},{mean_code},{max_err},{kind}\n")

    print(f"{'seg':>3} {'name':<18} {'Vin[mV]':>9} {'ideal':>7} {'dens[%]':>8} "
          f"{'mean':>7} {'maxerr':>7}")
    print("-" * 70)
    worst = 0
    for idx, name, vin_mv, exp, dens, mean_code, max_err, kind in rows:
        print(f"{idx:>3} {name:<18} {vin_mv:>9.1f} {exp:>7} {dens:>8.4f} "
              f"{mean_code:>7} {max_err:>7}")
        if kind == 0:
            worst = max(worst, max_err)
    print("-" * 70)
    print(f"total bits : {nbits_total}  ({nbits_total/16e6*1e3:.3f} ms @16 MHz CLKIN)")
    print(f"worst linear-segment error vs ideal model : {worst} LSB")
    print(f"written    : {mem_path}")
    print(f"             {vh_path}")
    if worst > 12:
        print("WARNING: model error exceeds the testbench default tolerance (8 LSB).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
