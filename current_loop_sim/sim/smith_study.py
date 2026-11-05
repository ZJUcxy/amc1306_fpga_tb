# -*- coding: utf-8 -*-
"""
Smith predictor for the 5 kHz current loop: transfer functions, Bode analysis
and closed-loop simulation.

Loop as built
-------------
    r --> C(s) --> u --> [ Vdc ] --> Gp(s) = 1/(R+Ls) --> i
                  ^                                        |
                  |                                        v
                  +-------- e^{-sT}  <----- sensor chain (sinc3 + 51-tap FIR)
                                ^
                                |   T = 24 us + 400 us + control sampling

with the Smith predictor the controller becomes

    Ca(s) = C(s) / (1 + C(s) Gm(s) (1 - e^{-sTm}))

where Gm(s) = Vdc/(Rm + Lm s) is the internal model.  If Gm = Gp*Vdc and
Tm = T, the closed-loop denominator collapses to 1 + C(s)Gm(s), i.e. the delay
disappears from the characteristic equation.

This script
-----------
  1. evaluates L(jw) for several configurations and reports crossover, phase
     margin, gain margin, closed-loop bandwidth and sensitivity peak
  2. plots the Bode diagram of the loop gain, of |S| and of the closed loop
  3. runs the full non-linear simulation (delta-sigma sensor included) for the
     same configurations and writes the step responses

Usage:  python smith_study.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import amc1306_loop as L  # noqa: E402

RES = L.RES

T_TOTAL = 0.424e-3 + 0.5 / L.FS_CTRL     # 0.524 ms of transport delay
FC = 800.0                                # PI bandwidth used everywhere


# --------------------------------------------------------------- analysis ---
def C(w, fc):
    kp, ki = L.pi_gains(fc)
    return kp + ki / (1j * w)


def Gm(w):
    return L.VDC / (L.R_ARM + 1j * w * L.L_ARM)


def Lg(w, fc, smith=None, T=None):
    """Loop gain referred to the error signal (before C)."""
    s = 1j * w
    g = L.VDC / (L.R_ARM + s * L.L_ARM)      # duty -> current
    T = T_TOTAL if T is None else T
    d = np.exp(-s * T)                       # sensor + sampling
    c = C(w, fc)
    if smith is None:
        return c * g * d
    rm = L.R_ARM * smith.get("r", 1.0)
    lm = L.L_ARM * smith.get("l", 1.0)
    tm = (T_TOTAL if T is None else T) * smith.get("t", 1.0)
    gm = L.VDC / (rm + s * lm)
    ca = c / (1 + c * gm * (1 - np.exp(-s * tm)))
    return ca * g * d


def margins(fc, smith=None, fmin=1.0, fmax=200e3, T=None):
    w = 2 * np.pi * np.logspace(np.log10(fmin), np.log10(fmax), 200000)
    lg = Lg(w, fc, smith, T)
    mag = np.abs(lg)
    ph = np.unwrap(np.angle(lg)) * 180 / np.pi

    # crossover (|L| = 1)
    idx = np.nonzero(np.diff(np.sign(mag - 1.0)))[0]
    wc = w[idx[0]] if len(idx) else np.nan
    pm = 180 + np.interp(wc, w, ph) if len(idx) else np.nan

    # -180 deg crossing (gain margin)
    jdx = np.nonzero(np.diff(np.sign(ph + 180.0)))[0]
    if len(jdx):
        wpc = w[jdx[0]]
        gm_db = -20 * np.log10(np.interp(wpc, w, mag))
        fpc = wpc / 2 / np.pi
    else:
        wpc, gm_db, fpc = np.nan, np.inf, np.nan

    # closed loop and sensitivity
    t_cl = lg / (1 + lg)
    s_cl = 1 / (1 + lg)
    m = w >= 2 * np.pi * fmin
    mag_t = np.abs(t_cl[m])
    dc = mag_t[0] if len(mag_t) else 1.0
    below = np.nonzero(mag_t <= dc / np.sqrt(2))[0]
    f_bw = w[m][below[0]] / 2 / np.pi if len(below) else np.nan
    ms = float(np.max(np.abs(s_cl)))

    return dict(wc=wc / 2 / np.pi, pm=pm, gm_db=gm_db, f180=fpc,
                f_bw=f_bw, ms=ms, w=w, lg=lg, t_cl=t_cl, s_cl=s_cl)


def formatter(name, m):
    return (f"{name:<34}{m['wc']/1e3:>8.2f}k{m['pm']:>9.1f}"
            f"{m['gm_db']:>9.1f}{m['f_bw']:>9.0f}{m['ms']:>9.2f}")


def analyse():
    cfgs = [
        ("PI only, no delay (theory)", None, 0.0),
        ("PI + 0.524 ms delay", None, 1.0),
        ("Smith, perfect model", dict(r=1, l=1, t=1), 1.0),
        ("Smith, plant R,L +20 %", dict(r=1.2, l=1.2, t=1), 1.0),
        ("Smith, plant R,L -20 %", dict(r=0.8, l=0.8, t=1), 1.0),
        ("Smith, delay model -20 %", dict(r=1, l=1, t=0.8), 1.0),
        ("Smith, delay model +20 %", dict(r=1, l=1, t=1.2), 1.0),
        ("Smith, plant +50 %", dict(r=1.5, l=1.5, t=1), 1.0),
    ]
    print(f"PI bandwidth {FC:.0f} Hz, transport delay {T_TOTAL*1e6:.0f} us")
    print()
    print(f"{'configuration':<34}{'fc':>9}{'PM [deg]':>9}{'GM [dB]':>9}{'f-3dB':>9}{'Ms':>9}")
    print("-" * 80)
    out = []
    for name, sm, dscale in cfgs:
        m = margins(FC, sm, T=(0.0 if dscale == 0.0 else None))
        print(formatter(name, m))
        out.append((name, m))
    return out


def bode(cfgs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    colors = ["#2ca02c", "#d62728", "#1f77b4", "#9467bd"]
    labels = ["Smith, perfect model", "PI + delay", "Smith, plant +20 %",
              "Smith, delay model -20 %"]

    for axx in ax:
        axx.set_xscale("log")
        axx.grid(alpha=0.3, which="both")

    for lab, sm, col in zip(labels, [dict(r=1, l=1, t=1), None,
                                     dict(r=1.2, l=1.2, t=1), dict(r=1, l=1, t=0.8)],
                            colors):
        m = margins(FC, sm)
        f = m["w"] / 2 / np.pi
        sel = (f > 10) & (f < 50e3)
        ax[0].semilogx(f[sel], 20 * np.log10(np.abs(m["lg"][sel])), color=col, lw=1.3, label=lab)
        ax[1].semilogx(f[sel], np.unwrap(np.angle(m["lg"][sel])) * 180 / np.pi,
                       color=col, lw=1.3, label=lab)
        ax[2].semilogx(f[sel], 20 * np.log10(np.abs(m["s_cl"][sel]) + 1e-12),
                       color=col, lw=1.3, label=lab)

    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_ylabel("|L| [dB]")
    ax[0].set_title(f"Loop gain, PI {FC:.0f} Hz, AMC1306 + 51-tap FIR "
                    f"(T = {T_TOTAL*1e6:.0f} us)")
    ax[0].legend(fontsize=8, loc="upper right")
    ax[0].set_ylim(-60, 60)

    ax[1].axhline(-180, color="k", lw=0.8, ls="--")
    ax[1].set_ylabel("phase [deg]")
    ax[1].set_ylim(-720, 0)

    ax[2].axhline(0, color="k", lw=0.8)
    ax[2].set_ylabel("|S| [dB]")
    ax[2].set_xlabel("frequency [Hz]")
    ax[2].set_ylim(-40, 20)

    fig.tight_layout()
    p = os.path.join(RES, "smith_bode.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print(f"\nwrote {p}")


# ------------------------------------------------------------- simulation ---
def simulate_cases():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cases = [
        ("PI only (no Smith)", None),
        ("Smith, perfect model", dict(r=1, l=1, t=1)),
        ("Smith, plant R,L +20 %", dict(r=1.2, l=1.2, t=1)),
        ("Smith, delay model -20 %", dict(r=1, l=1, t=0.8)),
        ("Smith, plant R,L +50 %", dict(r=1.5, l=1.5, t=1)),
        ("Smith, delay model +20 %", dict(r=1, l=1, t=1.2)),
    ]
    print()
    print(f"{'case':<28}{'over [%]':>10}{'settle':>10}{'ripple':>10}")
    print("-" * 58)
    curves = {}
    for name, sm in cases:
        r = L.simulate(use_fir=True, fc_hz=FC, use_bits=True, seed=2, smith=sm)
        m = L.metrics(r)
        curves[name] = (r, m)
        print(f"{name:<28}{m['overshoot_pct']:>9.1f}%{m['settle_ms']:>9.2f}ms"
              f"{m['ripple_A']:>9.3f}")

    fig, ax = plt.subplots(figsize=(11, 6))
    cols = ["#d62728", "#1f77b4", "#9467bd", "#2ca02c", "#ff7f0e", "#8c564b"]
    for (name, (r, m)), c in zip(curves.items(), cols):
        ax.plot(r["t"] * 1e3, r["i"], lw=1.2, color=c, label=name)
    r0 = list(curves.values())[0][0]
    ax.step(r0["t"] * 1e3, r0["cmd"], where="post", color="k", ls="--",
            lw=1.0, label="command (5 kHz)")
    ax.set_xlim(5, 25)
    ax.set_xlabel("time [ms]")
    ax.set_ylabel("phase current [A]")
    ax.set_title(f"Smith predictor on the 5 kHz loop, PI {FC:.0f} Hz, 51-tap FIR included")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9, loc="lower right")
    fig.tight_layout()
    p = os.path.join(RES, "smith_step.png")
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print(f"wrote {p}")


if __name__ == "__main__":
    analyse()
    bode(None)
    simulate_cases()
