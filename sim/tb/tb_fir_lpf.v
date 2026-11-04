// ============================================================================
//  tb_fir_lpf  --  self-checking testbench for the 51-tap low-pass FIR
// ============================================================================
//  Drives fir_lpf back-to-back (one sample per clock, the maximum throughput),
//  so the whole run takes microseconds instead of the milliseconds the real
//  62.5 kSPS chain would need.  "Frequency" is therefore defined in the sample
//  domain: f_Hz / 62500 cycles per sample -- exactly what the on-hardware
//  filter sees.
//
//  Checks
//    1. impulse response == quantised coefficients from MATLAB (bit-exact)
//    2. DC gain == 1.000 (sum of coefficients == 2^15)
//    3. |H(f)| at 1 kHz / 2.5 kHz / 4 kHz / 12 kHz == the MATLAB response
//    4. step response: settles, no gross overshoot
//  and prints the before/after sample pairs so the change the filter makes is
//  visible without opening a waveform viewer.
// ============================================================================

`timescale 1ps/1ps

module tb_fir_lpf;

    localparam integer TAPS   = 51;
    localparam real    Fs     = 62500.0;      // sample rate of the real chain
    localparam real    PI     = 3.14159265358979323846;
    localparam integer AMAX   = 20000;        // test amplitude, ~0.61 FS
    localparam integer NSIN   = 512;          // samples per sine test
    localparam integer NMEAS  = 256;          // samples used for the amplitude estimate
    localparam integer CLK_HALF = 1953;

    `include "fir_spec.vh"                    // FIR_GAIN_* from MATLAB

    // coefficient table (same file the RTL includes)
    reg signed [15:0] coef [0:TAPS-1];
    `include "fir_coefs.vh"

    // ------------------------------------------------------------- clock ----
    reg clk = 1'b0;
    always #(CLK_HALF) clk = ~clk;

    reg rst_n    = 1'b0;
    reg valid_in = 1'b0;
    reg signed [15:0] din = 16'sd0;

    wire valid_out;
    wire signed [15:0] dout;

    fir_lpf u_fir (
        .clk       (clk),
        .rst_n     (rst_n),
        .valid_in  (valid_in),
        .din       (din),
        .valid_out (valid_out),
        .dout      (dout)
    );

    // ------------------------------------------------- capture --------------
    integer cap [0:8191];
    integer cap_n = 0;
    integer ins [0:8191];
    integer sent_n = 0;

    always @(posedge clk)
        if (valid_out) begin
            cap[cap_n] = $signed(dout);
            cap_n = cap_n + 1;
        end

    // ------------------------------------------------- driver tasks ---------
    task send(input integer v);
        begin
            @(negedge clk);
            valid_in = 1'b1;
            din      = v[15:0];
            ins[sent_n] = v;
            sent_n = sent_n + 1;
        end
    endtask

    task idle(input integer n);
        integer j;
        begin
            for (j = 0; j < n; j = j + 1) begin
                @(negedge clk);
                valid_in = 1'b0;
            end
        end
    endtask

    task clear_cap;
        begin
            cap_n  = 0;
            sent_n = 0;
        end
    endtask

    // ------------------------------------------------- bookkeeping ----------
    integer errors = 0;
    integer checks = 0;

    function integer abs_i(input integer v);
        begin
            abs_i = (v < 0) ? -v : v;
        end
    endfunction

    // min/max over cap[from .. cap_n-1]
    integer mn, mx, k;
    task measure(input integer from, output integer amp);
        begin
            mn =  1000000;
            mx = -1000000;
            for (k = from; k < cap_n; k = k + 1) begin
                if (cap[k] < mn) mn = cap[k];
                if (cap[k] > mx) mx = cap[k];
            end
            amp = (mx - mn) / 2;
        end
    endtask

    // ------------------------------------------------- tests ----------------
    integer t, n, amp, expect_imp, err, worst_imp;
    integer worst_dc, amp_out, gain_ok;
    real    fn, gmeas, gexp, gdb;

    // one sine test: returns the measured amplitude
    task run_sine(input real fnorm, input integer nsamp, output integer ampout);
        integer j, s;
        begin
            clear_cap;
            for (j = 0; j < nsamp; j = j + 1) begin
                s = $rtoi(AMAX * $sin(2.0 * PI * fnorm * j));
                send(s);
            end
            idle(2);
            measure(nsamp - NMEAS, ampout);
        end
    endtask

    task report_gain(input real f_hz, input integer ampout, input real gexp_r);
        begin
            gmeas = ampout / (AMAX * 1.0);
            checks = checks + 1;
            if ((gmeas - gexp_r) > 0.05 * gexp_r || (gexp_r - gmeas) > 0.05 * gexp_r) begin
                errors = errors + 1;
                $display("  FAIL  %7.0f Hz : gain %.4f, expected %.4f", f_hz, gmeas, gexp_r);
            end
            if (gmeas > 0.0) gdb = 20.0 * $log10(gmeas);
            else             gdb = -99.0;
            $display("  %7.0f Hz  in=%6d  out=%6d  gain=%.4f  expected %.4f  (%0.2f dB)",
                     f_hz, AMAX, ampout, gmeas, gexp_r, gdb);
        end
    endtask

    initial begin
        $display("");
        $display("==================================================================");
        $display(" tb_fir_lpf : 51-tap LPF, Fs = %.1f kHz, pass 1 kHz / stop 2.5 kHz", Fs/1e3);
        $display("==================================================================");

        rst_n = 1'b0;
        repeat (10) @(posedge clk);
        @(negedge clk); rst_n = 1'b1;

        // ---------------- 1. impulse response ------------------------------
        $display("");
        $display("[1] impulse response vs MATLAB coefficients");
        clear_cap;
        send(AMAX);
        for (k = 0; k < TAPS + 5; k = k + 1) send(0);   // clock the tail out
        idle(2);

        worst_imp = 0;
        if (cap_n < TAPS) begin
            errors = errors + 1;
            $display("  FAIL  only %0d outputs captured, expected at least %0d", cap_n, TAPS);
        end
        else begin
            for (k = 0; k < TAPS; k = k + 1) begin
                expect_imp = (AMAX * coef[k]) >>> 15;   // same shift the RTL does
                err        = cap[k] - expect_imp;
                if (abs_i(err) > worst_imp) worst_imp = abs_i(err);
            end
        end
        checks = checks + 1;
        if (worst_imp > 1) begin
            errors = errors + 1;
            $display("  FAIL  worst |err| = %0d LSB against the coefficient table", worst_imp);
        end
        $display("  taps=%0d  worst |err| = %0d LSB  (0 expected: the RTL uses the same shift)",
                 TAPS, worst_imp);
        $display("  first 6 outputs: %0d %0d %0d %0d %0d %0d",
                 cap[0], cap[1], cap[2], cap[3], cap[4], cap[5]);
        $display("  coef  scaled   : %0d %0d %0d %0d %0d %0d",
                 (AMAX*coef[0])>>>15, (AMAX*coef[1])>>>15, (AMAX*coef[2])>>>15,
                 (AMAX*coef[3])>>>15, (AMAX*coef[4])>>>15, (AMAX*coef[5])>>>15);

        // ---------------- 2. DC gain ---------------------------------------
        $display("");
        $display("[2] DC gain (sum of coefficients = 2^15 -> unity)");
        clear_cap;
        for (n = 0; n < 200; n = n + 1) send(10000);
        idle(2);
        worst_dc = 0;
        for (k = 150; k < 200; k = k + 1)
            if (abs_i(cap[k] - 10000) > worst_dc) worst_dc = abs_i(cap[k] - 10000);
        checks = checks + 1;
        if (worst_dc > 2) begin
            errors = errors + 1;
            $display("  FAIL  settled output is %0d, expected 10000 +/-2", cap[199]);
        end
        $display("  in=10000  settled out=%0d  worst |err| over last 50 = %0d LSB",
                 cap[199], worst_dc);

        // ---------------- 3. frequency response ----------------------------
        $display("");
        $display("[3] sine response (amplitude %0d, %0d samples each)", AMAX, NSIN);
        $display("  ---------------------------------------------------------------");

        fn = 1000.0 / Fs;  run_sine(fn, NSIN, amp_out);
        report_gain(1000.0, amp_out, FIR_GAIN_1K0);

        fn = 2500.0 / Fs;  run_sine(fn, NSIN, amp_out);
        report_gain(2500.0, amp_out, FIR_GAIN_2K5);

        fn = 4000.0 / Fs;  run_sine(fn, NSIN, amp_out);
        report_gain(4000.0, amp_out, FIR_GAIN_4K0);

        fn = 12000.0 / Fs; run_sine(fn, NSIN, amp_out);
        report_gain(12000.0, amp_out, FIR_GAIN_12K0);
        $display("  ---------------------------------------------------------------");

        // ---------------- 4. before/after sample table (4 kHz) -------------
        $display("");
        $display("[4] before / after, 4 kHz sine (16 consecutive samples)");
        $display("   n    before    after   change");
        fn = 4000.0 / Fs;
        clear_cap;
        for (n = 0; n < NSIN; n = n + 1) send($rtoi(AMAX * $sin(2.0 * PI * fn * n)));
        idle(2);
        for (n = 300; n < 316; n = n + 1)
            $display("  %3d  %6d  %6d  %7d", n, ins[n], cap[n], cap[n] - ins[n]);

        // ---------------- 5. step response ---------------------------------
        $display("");
        $display("[5] step response 0 -> %0d", AMAX);
        clear_cap;
        for (n = 0; n < 100; n = n + 1) send(0);
        for (n = 0; n < 100; n = n + 1) send(AMAX);
        idle(2);

        mn = 1000000; mx = -1000000;
        for (k = 100; k < cap_n; k = k + 1) begin
            if (cap[k] < mn) mn = cap[k];
            if (cap[k] > mx) mx = cap[k];
        end
        checks = checks + 1;
        if (mx > AMAX * 105 / 100) begin
            errors = errors + 1;
            $display("  FAIL  overshoot: peak %0d > 5 %% over %0d", mx, AMAX);
        end
        // a linear-phase FIR rings symmetrically: allow up to -10 % pre-ringing
        checks = checks + 1;
        if (mn < -AMAX / 10) begin
            errors = errors + 1;
            $display("  FAIL  undershoot: min %0d below -10 %%", mn);
        end
        $display("  peak=%0d (%.1f %% of step)  min=%0d  final=%0d",
                 mx, 100.0 * mx / AMAX, mn, cap[cap_n-1]);
        $display("  group delay = (taps-1)/2 = %0d samples = %.3f ms at %.1f kHz",
                 (TAPS-1)/2, (TAPS-1)/2.0/Fs*1e3, Fs/1e3);

        // ---------------- summary ------------------------------------------
        $display("");
        $display("==================================================================");
        $display(" checks: %0d   errors: %0d", checks, errors);
        if (errors == 0) $display(" RESULT: PASS");
        else             $display(" RESULT: FAIL");
        $display("==================================================================");
        $display("");
        $finish;
    end

    initial begin
        repeat (2000000) @(posedge clk);
        $display("ERROR: watchdog timeout");
        $finish;
    end

endmodule
