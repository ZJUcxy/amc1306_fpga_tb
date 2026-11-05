# -*- coding: utf-8 -*-
"""
25 kHz PWM / 5 kHz current-loop simulation with the AMC1306 sampling chain.

System under test
-----------------
    I* (5 kHz step) --> PI (5 kHz) --> PWM (25 kHz) --> R-L plant
                            ^                              |
                            |                              |
                      51-tap FIR <-- sinc3 OSR 256 <-- AMC1306 (1-bit delta-sigma)

The sensor path is the same bit-exact model that was validated against the RTL
in ../amc1306_tb (modulator density = 0.5 + VIN/640 mV, CIC 32-bit wrapping
arithmetic, FIR (sum coef * x) >> 15).  Nothing in the loop is idealised except
the R-L plant itself.

Everything runs off one 1 us time step, so the PWM ripple, the ADC sampling
instants and the filter delay all fall out of the model instead of being
hand-waved into a "delay" term.

Usage
-----
    python amc1306_loop.py                  # run all four cases, write results/
    python amc1306_loop.py --fast           # ideal sensor (no delta-sigma bits)
"""

import argparse
import csv
import math
import os
import random

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RES = os.path.join(ROOT, "results")

# --------------------------------------------------------------------------
# parameters
# --------------------------------------------------------------------------
FS_CTRL = 5_000.0          # current loop update rate   (200 us)
FS_PWM = 25_000.0          # carrier / switching rate   (40 us)
FS_ADC = 62_500.0          # AMC1306 chain output rate  (16 us = 256 CLKIN @16 MHz)
VDC = 24.0                 # dc-link voltage [V]
R_ARM = 0.25               # armature resistance [ohm]
L_ARM = 2.0e-3             # armature inductance [H]
R_SHUNT = 0.025            # shunt [ohm] -> 10 A = 250 mV = AMC1306 FS
                           # (0.05 ohm would put the 5 A step exactly at FS,
                           #  where the modulator clips -- that is what made the
                           #  first run of this script oscillate)
I_FS = 0.250 / R_SHUNT     # 10.0 A full scale
I_CODE_PER_A = 25600.0 / I_FS   # 2560 LSB/A

DT = 0.25e-6               # integration step [s] (PWM edge resolution = 1 % duty)
T_SIM = 0.060              # 60 ms
T_ARM = 0.002              # loop stays open until the sensor chain has settled
                           # (the sinc3 CIC needs 3 windows, the FIR 51 samples)
T_STEP_UP = 0.010          # 0 -> 5 A at 10 ms
T_STEP_DN = 0.040          # 5 -> 2 A at 40 ms
I_HI, I_LO = 5.0, 2.0

# PI gains, per-unit on duty (see README for the design)
#   Kp = wc*L/Vdc ,  Ki = Kp*R/L  (pole-zero cancellation)
def pi_gains(fc_hz):
    wc = 2.0 * math.pi * fc_hz
    kp = wc * L_ARM / VDC
    ki = kp * R_ARM / L_ARM
    return kp, ki

# ---------------------------------------------------------------- plant ----
class Plant:
    """R-L load, di/dt = (v - R i) / L.  Euler at 1 us is ample (tau = 8 ms)."""

    def __init__(self):
        self.i = 0.0

    def step(self, v, dt):
        self.i += (v - R_ARM * self.i) * dt / L_ARM
        return self.i


class Pwm:
    """Triangular carrier, 25 kHz.  Returns the switched voltage (0 or Vdc)."""

    def __init__(self, f=FS_PWM):
        self.period = 1.0 / f
        self.t = 0.0

    def __call__(self, duty, dt):
        self.t = (self.t + dt) % self.period
        ph = self.t / self.period
        tri = 2.0 * ph if ph < 0.5 else 2.0 * (1.0 - ph)   # 0 -> 1 -> 0
        return VDC if tri < duty else 0.0


# --------------------------------------------------------------- sensor ----
class Amc1306Chain:
    """AMC1306 (1-bit, 16 MHz) + sinc3 OSR 256 + optional 51-tap FIR.

    Bit-exact twin of rtl/amc1306_sample_stub.v and rtl/fir_lpf.v.
    """

    def __init__(self, coef, use_fir=True, use_bits=True, seed=1):
        self.coef = coef
        self.use_fir = use_fir
        self.use_bits = use_bits
        self.rng = random.Random(seed)

        # modulator state
        self.v1 = self.v2 = 0.0
        # CIC state (32-bit wrap, exactly like the RTL)
        self.i1 = self.i2 = self.i3 = 0
        self.z1 = self.z2 = self.z3 = 0
        self.dec = 0
        # FIR state
        self.hist = [0] * len(coef)
        self.out = 0

    @staticmethod
    def _w32(x):
        x &= 0xFFFFFFFF
        return x - 0x100000000 if x & 0x80000000 else x

    def _modulate(self, vin, n=256):
        """2nd-order 1-bit loop; density = 0.5 + VIN/640 mV (VClipping = 320 mV)."""
        v_clip = 0.320
        x = vin / v_clip
        if x > 1.0:
            x = 1.0
        elif x < -1.0:
            x = -1.0
        v1, v2 = self.v1, self.v2
        g = self.rng.gauss
        bits = np.empty(n, dtype=np.int8)
        for k in range(n):
            xn = x + g(0.0, 12e-6 / v_clip)          # 12 uV rms input noise
            y = 1 if v2 >= 0.0 else 0
            fb = 1.0 if y else -1.0
            v1 += 0.5 * (xn - fb)
            v2 += 0.4 * (v1 - fb)
            bits[k] = y
        self.v1, self.v2 = v1, v2
        return bits

    def _cic(self, bit):
        i1n = self._w32(self.i1 + bit)
        i2n = self._w32(self.i2 + self.i1 + bit)
        i3n = self._w32(self.i3 + self.i2 + self.i1 + bit)
        d1 = self._w32(i3n - self.z1)
        d2 = self._w32(d1 - self.z2)
        d3 = self._w32(d2 - self.z3)
        self.i1, self.i2, self.i3 = i1n, i2n, i3n
        if self.dec == 255:
            self.z1, self.z2, self.z3 = i3n, d1, d2
            self.dec = 0
            code = (d3 >> 8) - 32768
            return max(-32768, min(32767, code))
        self.dec += 1
        return None

    def sample(self, amps):
        """One 16 us ADC period: current in amps -> 16-bit code (or None)."""
        vin = amps * R_SHUNT
        if self.use_bits:
            bits = self._modulate(vin)
            code = None
            for b in bits:
                c = self._cic(int(b))
                if c is not None:
                    code = c
            if code is None:
                return None
        else:
            # fast path: ideal transfer function + one LSB of quantisation noise
            code = int(round(amps * I_CODE_PER_A + self.rng.gauss(0.0, 0.5)))
            code = max(-32768, min(32767, code))

        if not self.use_fir:
            self.out = code
            return code

        # 51-tap FIR, bit-exact (full precision MAC, >>15, saturate)
        c = self.coef
        acc = code * c[0] + sum(self.hist[k - 1] * c[k] for k in range(1, len(c)))
        self.hist = [code] + self.hist[:-1]
        y = acc >> 15
        self.out = max(-32768, min(32767, y))
        return self.out


# ----------------------------------------------------------- controller ----
class PI:
    def __init__(self, kp, ki, out_min=0.0, out_max=1.0):
        self.kp, self.ki = kp, ki
        self.integ = 0.0
        self.out_min, self.out_max = out_min, out_max

    def step(self, err, ts, ff=0.0):
        u = self.kp * err + self.integ + ff
        if u > self.out_max:                       # conditional anti-windup
            u = self.out_max
            if err < 0:
                self.integ += self.ki * err * ts
        elif u < self.out_min:
            u = self.out_min
            if err > 0:
                self.integ += self.ki * err * ts
        else:
            self.integ += self.ki * err * ts
        return u


# ------------------------------------------------------------- scenario ----
def load_coef():
    """Read the 51-tap coefficients from the FPGA build (single source of truth).

    Searched upwards, so the script works both when it is a sibling of sim/fir
    (standalone checkout) and when it sits below it (inside amc1306_tb).
    """
    import re
    p = HERE
    for _ in range(5):
        cand = os.path.join(p, "sim", "fir", "amc1306_lpf_51tap.coe")
        if os.path.exists(cand):
            txt = open(cand, encoding="ascii").read().split("coefdata=")[1]
            return [int(m) for m in re.findall(r"(-?\d+)\s*[,;]", txt)]
        p = os.path.dirname(p)
    raise FileNotFoundError("amc1306_lpf_51tap.coe not found above " + HERE)


def command_at(t):
    if t < T_STEP_UP:
        return 0.0
    if t < T_STEP_DN:
        return I_HI
    return I_LO


def simulate(use_fir, fc_hz, use_bits=True, seed=1, coef=None):
    coef = coef if coef is not None else load_coef()
    plant = Plant()
    pwm = Pwm()
    sensor = Amc1306Chain(coef, use_fir=use_fir, use_bits=use_bits, seed=seed)
    kp, ki = pi_gains(fc_hz)
    pi = PI(kp, ki)
    # feed-forward: duty that produces the commanded current in steady state
    ff_gain = (R_ARM / VDC)

    n = int(round(T_SIM / DT))
    t = np.zeros(n)
    ia = np.zeros(n)
    duty = np.zeros(n)
    cmd = np.zeros(n)
    meas = np.zeros(n)

    next_adc = 0.0
    next_ctrl = 0.0
    cur_duty = 0.0
    adc_val = 0.0          # newest ADC conversion, in amps
    ctrl_meas = 0.0        # value latched at the carrier valley (real drives do this)
    prev_phase = 0.0

    for k in range(n):
        tk = k * DT
        v = pwm(cur_duty, DT)
        plant.step(v, DT)

        # latch the measurement once per PWM period, at a fixed carrier phase,
        # so the switching ripple never aliases into the current loop
        if pwm.t < prev_phase:
            ctrl_meas = adc_val
        prev_phase = pwm.t

        if tk + DT >= next_adc - 1e-12:
            next_adc += 1.0 / FS_ADC
            s = sensor.sample(plant.i)
            if s is not None:
                adc_val = s / I_CODE_PER_A     # back to amps

        if tk + DT >= next_ctrl - 1e-12:
            next_ctrl += 1.0 / FS_CTRL
            if tk < T_ARM:
                # keep the bridge off while the sinc3/FIR pipeline fills up,
                # otherwise the CIC priming samples look like a huge current
                cur_duty = 0.0
                pi.integ = 0.0
            else:
                ref = command_at(tk)
                err = ref - ctrl_meas
                cur_duty = pi.step(err, 1.0 / FS_CTRL, ff=ref * ff_gain)

        t[k] = tk
        ia[k] = plant.i
        duty[k] = cur_duty
        cmd[k] = command_at(tk)
        meas[k] = ctrl_meas

    return dict(t=t, i=ia, duty=duty, cmd=cmd, meas=meas)


# --------------------------------------------------------------- metrics ---
def smooth(x, n):
    """Moving average over n samples (one control period) to remove PWM ripple."""
    if n <= 1:
        return x
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def metrics(r, t0=T_STEP_UP, t1=T_STEP_DN, target=I_HI):
    t, i_raw = r["t"], r["i"]
    i = smooth(i_raw, int(round(1.0 / FS_CTRL / DT)))      # ripple-free view
    m = (t >= t0) & (t < t1)
    seg_t, seg_i = t[m], i[m]
    i0 = 0.0
    final = np.mean(seg_i[-int(0.002 / DT):])

    # rise time 10-90 % of the step
    lo, hi = i0 + 0.1 * (target - i0), i0 + 0.9 * (target - i0)
    t_lo = seg_t[np.argmax(seg_i >= lo)] if np.any(seg_i >= lo) else np.nan
    t_hi = seg_t[np.argmax(seg_i >= hi)] if np.any(seg_i >= hi) else np.nan
    rise_ms = (t_hi - t_lo) * 1e3 if not (np.isnan(t_lo) or np.isnan(t_hi)) else float("nan")

    peak = float(np.max(seg_i))
    overshoot = 100.0 * (peak - target) / (target - i0)

    # settling: last time the smoothed response leaves the +/-2 % band
    band = 0.02 * (target - i0)
    outside = np.abs(seg_i - target) > band
    if np.any(outside):
        last = np.nonzero(outside)[0][-1]
        settle_ms = (seg_t[last] - t0) * 1e3
    else:
        settle_ms = 0.0

    ss_err = 100.0 * (target - final) / (target - i0) if target else 0.0

    # raw ripple over the last 5 ms before the down step
    tail_raw = i_raw[m][-int(0.005 / DT):]
    ripple_pp = float(np.max(tail_raw) - np.min(tail_raw))

    # oscillation detector: count zero crossings of (i - final) in the segment
    dev = seg_i - target
    zc = int(np.sum(np.diff(np.sign(dev)) != 0))

    return dict(rise_ms=rise_ms, overshoot_pct=overshoot, settle_ms=settle_ms,
                ss_err_pct=ss_err, ripple_A=ripple_pp, final_A=final, zc=zc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true",
                    help="case A only: skip the delta-sigma bits (ideal sensor)")
    a = ap.parse_args()

    os.makedirs(RES, exist_ok=True)

    # 800 Hz is a sane bandwidth for a 5 kHz loop with a light sensor chain;
    # 230 Hz is what is left once the 51-tap FIR adds 0.40 ms of group delay.
    cases = [
        ("A  ideal sensor",              dict(use_fir=False, fc=800.0, bits=False)),
        ("B  AMC1306 + sinc3",           dict(use_fir=False, fc=800.0, bits=True)),
        ("C  + 51-tap FIR (same gains)", dict(use_fir=True,  fc=800.0, bits=True)),
        ("D  + 51-tap FIR (retuned)",    dict(use_fir=True,  fc=150.0, bits=True)),
    ]

    rows = []
    curves = {}
    for name, kw in cases:
        r = simulate(use_fir=kw["use_fir"], fc_hz=kw["fc"], use_bits=kw["bits"], seed=2)
        curves[name] = r
        mm = metrics(r)
        rows.append((name, kw["fc"], mm))
        print(f"{name:<30} fc={kw['fc']:5.0f} Hz  "
              f"rise={mm['rise_ms']:6.2f} ms  over={mm['overshoot_pct']:6.1f} %  "
              f"settle={mm['settle_ms']:6.2f} ms  ss={mm['ss_err_pct']:+5.1f} %  "
              f"ripple={mm['ripple_A']:5.3f} A  zc={mm['zc']}")

    # ------------------------------------------------------------- csv ----
    with open(os.path.join(RES, "metrics.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case", "pi_bandwidth_Hz", "rise_ms", "overshoot_pct",
                    "settle_ms", "ss_err_pct", "ripple_A", "final_A"])
        for name, fc, mm in rows:
            w.writerow([name, f"{fc:.0f}", f"{mm['rise_ms']:.3f}",
                        f"{mm['overshoot_pct']:.2f}", f"{mm['settle_ms']:.2f}",
                        f"{mm['ss_err_pct']:.2f}", f"{mm['ripple_A']:.4f}",
                        f"{mm['final_A']:.4f}"])

    # ----------------------------------------------------------- plots ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6))
    colors = {"A": "0.55", "B": "#1f77b4", "C": "#d62728", "D": "#2ca02c"}
    for name, _, _ in rows:
        r = curves[name]
        ax.plot(r["t"] * 1e3, r["i"], lw=1.1, color=colors[name[0]], label=name.strip())
    ax.step(curves[rows[0][0]]["t"] * 1e3, curves[rows[0][0]]["cmd"], where="post",
            color="k", ls="--", lw=1.0, label="command (5 kHz)")
    ax.set_xlabel("time [ms]")
    ax.set_ylabel("phase current [A]")
    ax.set_title("Current-loop step response, 25 kHz PWM / 5 kHz loop, AMC1306 sensing")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9, loc="lower right")
    ax.set_xlim(5, 45)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, "step_response.png"), dpi=140)
    plt.close(fig)

    # zoom: steady state ripple, one control period
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for name, _, _ in rows:
        r = curves[name]
        m = (r["t"] > 0.030) & (r["t"] < 0.0304)
        ax.plot(r["t"][m] * 1e3, r["i"][m], lw=1.3, color=colors[name[0]],
                label=name.strip())
    ax.set_xlabel("time [ms]")
    ax.set_ylabel("phase current [A]")
    ax.set_title("steady state zoom (0.4 ms = 10 PWM periods = 2 control periods)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(RES, "steady_state_zoom.png"), dpi=140)
    plt.close(fig)

    print(f"\nwrote {os.path.join(RES, 'metrics.csv')}")
    print(f"wrote {os.path.join(RES, 'step_response.png')}")
    print(f"wrote {os.path.join(RES, 'steady_state_zoom.png')}")


if __name__ == "__main__":
    main()
