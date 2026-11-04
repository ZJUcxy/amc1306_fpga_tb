// ============================================================================
//  tb_amc1306_sample  --  self-checking testbench for the AMC1306 sampling
//                        chain + 51-tap low-pass FIR (Vivado 2022.2 / xsim)
// ============================================================================
//  Instantiates amc1306_chain:
//      amc1306_sample --> valid_pre / data_pre --> fir_lpf --> valid_post / data_post
//
//  Every value check below is done on the *pre*-filter stream, i.e. exactly the
//  ports of the bare AMC1306 module, so this file still doubles as the module's
//  own testbench.  The FIR is checked in depth by tb_fir_lpf; here only its
//  settled DC value and its valid rate are asserted (the DC sweep deliberately
//  uses 8 samples per step, far too short for a 51-tap filter to settle).
//
//  DUT interface (must match amc1306_sample):
//      clk    256 MHz system clock
//      rst_n  asynchronous active-low reset
//      en     1 = sampling enabled, 0 = outputs held, valid low
//      bit_in AMC1306 DOUT (1-bit delta-sigma stream, 16 MHz)
//      valid  one clk pulse per output sample (62.5 kSPS)
//      data   16-bit corrected code, two's complement, 0 = 0 A
//
//  Stimulus is NOT a synthetic square wave: it is a real 1-bit delta-sigma
//  stream produced by ../gen/gen_bitstream.py from the AMC1306M25 transfer
//  function (density = 0.5 + VIN/640 mV, 127+1 clipping pattern above
//  +/-320 mV), one bit per CLKIN cycle, with thermal noise, input offset and
//  the fCLKIN/32 chopper ripple modelled in.
//
//  Run:
//      xsim tb_amc1306_sample -testplusarg "MEM=<path>/scenario.mem"
//  or use ../scripts/run_xsim.bat, which does it for you.
// ============================================================================

`timescale 1ps/1ps

module tb_amc1306_sample;

    // scenario geometry and expected codes, generated together with the .mem
    `include "scenario_params.vh"

    // ------------------------------------------------------------- knobs ----
    localparam integer DIV       = 16;      // 256 MHz / 16 = 16 MHz CLKIN
    localparam integer OSR       = 256;     // sinc3 decimation ratio
    localparam integer SETTLE    = 4;       // samples skipped after a level step
    localparam integer TOL_LSB   = 8;       // allowed |data - ideal| on linear points
    localparam integer CLIP_MIN  = 24000;   // over-range must exceed +/-250 mV FS
    localparam integer TD_PS     = 15000;   // AMC1306 tD max before DOUT is valid
    localparam integer CLK_HALF  = 1953;    // 3.906 ns period  (~256 MHz)
    localparam integer EN_OFF    = 600;     // CLKIN cycles en is held low
    localparam integer WINDOWS   = SEG_BITS / OSR;

    // Expected value = ideal code * EXP_NUM / EXP_DEN.  Default 1/1 matches an
    // AMC1306-ideal transfer function (full scale = +/-25600 counts).
    // If your DUT re-normalises so that +/-250 mV maps to +/-32767, use
    //   EXP_NUM = 32767, EXP_DEN = 25600
    localparam integer EXP_NUM = 1;
    localparam integer EXP_DEN = 1;

    // ------------------------------------------------------------- clock ----
    // 256 MHz requested; 2*1953 ps = 3.906 ns = 255.984 MHz (-0.016 %).
    // Exact 3.90625 ns would need a 1 fs time precision, which buys nothing
    // here: every check is cycle-count based.
    reg clk = 1'b0;
    always #(CLK_HALF) clk = ~clk;

    reg rst_n = 1'b0;
    reg en    = 1'b0;

    wire        valid;
    wire [15:0] data;

    // CLKIN replica. The DUT divides clk by DIV internally with the same
    // convention (counter cleared by rst_n, rising edge at count 0); this
    // replica drives the bitstream model. See README if your DUT exposes
    // clk_out -- then connect that instead and delete this block.
    reg  [3:0] div_cnt = 4'd0;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) div_cnt <= 4'd0;
        else        div_cnt <= (div_cnt == DIV-1) ? 4'd0 : div_cnt + 4'd1;

    wire clkin = ~div_cnt[3];               // 16 MHz, 50 % duty

    // bit replayed from the .mem file, launched on the CLKIN rising edge
    reg dout = 1'b0;

    wire                     valid_post;    // after the 51-tap low-pass FIR
    wire signed [15:0]       data_post;

    // ------------------------------------------------------- DUT ------------
    // amc1306_sample -> fir_lpf.  `valid`/`data` below are the *pre*-filter
    // signals, exactly the ports of the bare AMC1306 module, so every check in
    // this testbench still applies unchanged.  The post-filter stream is
    // captured separately (section 7) and only the settled values are asserted,
    // because a 51-tap FIR needs 51 samples to settle and the DC sweep uses
    // 8 samples per step on purpose (to keep the run short).
    amc1306_chain u_chain (
        .clk        (clk),
        .rst_n      (rst_n),
        .en         (en),
        .bit_in     (dout),
        .valid_pre  (valid),
        .data_pre   (data),
        .valid_post (valid_post),
        .data_post  (data_post)
    );

    // ------------------------------------------- bitstream replay -----------
    reg     bit_mem [0:BIT_DEPTH-1];
    integer bit_cnt = 0;
    reg [8*260-1:0] mem_file;
    integer fd;

    initial begin
        if (!$value$plusargs("MEM=%s", mem_file)) mem_file = "scenario.mem";
        fd = $fopen(mem_file, "r");
        if (fd == 0) begin
            $display("ERROR: cannot open the bitstream file '%0s'", mem_file);
            $display("       pass  -testplusarg MEM=<absolute path>/scenario.mem");
            $finish;
        end
        $fclose(fd);
        $display("[%0t ps] TB: loading bitstream %0s", $time, mem_file);
        $readmemb(mem_file, bit_mem);
        if (bit_mem[0] === 1'bx) begin
            $display("ERROR: bitstream file is empty or not a 0/1 per line dump");
            $finish;
        end
        dout = bit_mem[0];
    end

    // one bit per CLKIN cycle, launched on the rising edge with the datasheet
    // valid delay tD; wraps back to the first bit so long runs never run dry.
    // tail_mode freezes the stream inside segment 0 (-250 mV) so the 51-tap FIR
    // has a constant level to settle on for the final check.
    reg  tail_mode = 1'b0;
    wire [31:0] next_idx = tail_mode ? (bit_cnt % SEG_BITS)
                                     : ((bit_cnt >= BIT_DEPTH-1) ? 32'd0 : bit_cnt + 1);

    always @(posedge clkin)
        if (rst_n) begin
            bit_cnt <= next_idx;
            dout    <= #(TD_PS) bit_mem[next_idx];
        end

    wire [31:0] cur_seg = bit_cnt / SEG_BITS;

    // ------------------------------------------------------- scoreboard -----
    integer   seg_expected [0:NUM_SEG-1];
    integer   seg_kind     [0:NUM_SEG-1];
    real      seg_vin_mv   [0:NUM_SEG-1];
    reg [8*20-1:0] seg_name [0:NUM_SEG-1];

    `include "scenario_table.vh"

    integer checks = 0;
    integer errors = 0;
    integer max_abs = 0;
    integer printed = 0;
    integer last_seg = -1;
    integer skip_next = SETTLE;
    integer resume_skip = 0;

    integer s_n  [0:NUM_SEG-1];
    integer s_sum[0:NUM_SEG-1];
    integer s_max[0:NUM_SEG-1];
    integer s_err[0:NUM_SEG-1];
    integer s_vc [0:NUM_SEG-1];

    integer viol_en  = 0;                   // valid while disabled
    integer viol_rst = 0;                   // valid during reset
    reg     rate_en  = 1'b1;                // output-rate counting enabled
    integer fir_expect = 0, fir_err = 0, fir_abs = 0, fir_fail = 0;
    integer i;

    initial begin
        for (i = 0; i < NUM_SEG; i = i + 1) begin
            s_n[i] = 0; s_sum[i] = 0; s_max[i] = 0; s_err[i] = 0; s_vc[i] = 0;
        end
    end

    integer k, exp_v, err_v, abs_v, got_v, got_abs;

    always @(posedge clk) begin
        if (valid === 1'b1 && rst_n === 1'b1) begin
            k = cur_seg;
            if (k != last_seg) begin
                last_seg  = k;
                skip_next = SETTLE;         // filter settling after the step
            end
            if (resume_skip > 0) begin
                resume_skip = resume_skip - 1;
                skip_next   = 0;
            end
            if (rate_en) s_vc[k] = s_vc[k] + 1;
            if (skip_next > 0) begin
                skip_next = skip_next - 1;
            end
            else begin
                got_v   = $signed(data);
                exp_v   = (seg_expected[k] * EXP_NUM) / EXP_DEN;
                err_v   = got_v - exp_v;
                abs_v   = (err_v < 0) ? -err_v : err_v;
                got_abs = (got_v < 0) ? -got_v : got_v;

                checks = checks + 1;
                s_n[k]   = s_n[k]   + 1;
                s_sum[k] = s_sum[k] + got_v;
                if (abs_v > s_max[k]) s_max[k] = abs_v;
                if (abs_v > max_abs)  max_abs  = abs_v;

                if (seg_kind[k] == 2) begin
                    // over-range: only "beyond +/-250 mV full scale" is checked
                    if (got_abs < CLIP_MIN) begin
                        errors  = errors + 1;
                        s_err[k] = s_err[k] + 1;
                    end
                end
                else if (abs_v > TOL_LSB) begin
                    errors  = errors + 1;
                    s_err[k] = s_err[k] + 1;
                end

                if (abs_v > TOL_LSB && printed < 20) begin
                    $display("  MISMATCH seg %0d %0s Vin=%0.1f mV : exp=%0d got=%0d err=%0d",
                             k, seg_name[k], seg_vin_mv[k], exp_v, got_v, err_v);
                    printed = printed + 1;
                end
            end
        end
    end

    // ------------------------------------------- post-filter observation ---
    integer post_n    = 0;
    integer post_last = 0;
    integer pre_last  = 0;
    always @(posedge clk) begin
        if (valid === 1'b1) begin
            pre_last = $signed(data);
        end
        if (valid_post === 1'b1 && rst_n === 1'b1) begin
            post_n    = post_n + 1;
            post_last = $signed(data_post);
        end
    end

    // one-cycle grace: a pulse that started before en/rst_n dropped is legal,
    // so only flag valid held high once en/rst_n has already been low a cycle
    reg en_q = 1'b0, rst_q = 1'b0;
    always @(posedge clk) begin
        en_q  <= en;
        rst_q <= rst_n;
        if (valid === 1'b1 && en    === 1'b0 && en_q  === 1'b0) viol_en  = viol_en  + 1;
        if (valid === 1'b1 && rst_n === 1'b0 && rst_q === 1'b0) viol_rst = viol_rst + 1;
    end

    // ------------------------------------------------------------ report ----
    task report;
        integer j, mean, rate_bad;
        begin
            $display("");
            $display("------------------------------------------------------------------");
            $display(" seg  name                   Vin[mV]   ideal   measured  max|err|  n  fail");
            $display("------------------------------------------------------------------");
            rate_bad = 0;
            for (j = 0; j < NUM_SEG; j = j + 1) begin
                mean = (s_n[j] > 0) ? (s_sum[j] / s_n[j]) : 0;
                $display(" %3d  %0s %8.1f %7d %10d %9d %3d %5d",
                         j, seg_name[j], seg_vin_mv[j],
                         (seg_expected[j] * EXP_NUM) / EXP_DEN,
                         mean, s_max[j], s_n[j], s_err[j]);
                if (s_vc[j] < WINDOWS-1 || s_vc[j] > WINDOWS+1) begin
                    $display("      ^ valid pulses in this segment: %0d, expected %0d",
                             s_vc[j], WINDOWS);
                    rate_bad = rate_bad + 1;
                end
            end
            $display("------------------------------------------------------------------");

            $display(" checks            : %0d", checks);
            $display(" value errors      : %0d", errors);
            $display(" worst |err|       : %0d LSB (tolerance %0d)", max_abs, TOL_LSB);
            $display(" valid output rate : %0d wrong segments (expect %0d samples/seg)",
                     rate_bad, WINDOWS);
            $display(" en gating         : %0d violations of 'valid must be low when en=0'", viol_en);
            $display(" reset             : %0d violations of 'valid must be low in reset'", viol_rst);
            $display(" FIR valid pulses  : %0d", post_n);
            $display(" FIR settled sample: pre %0d -> post %0d (expected %0d), |err| %0d LSB",
                     pre_last, post_last, fir_expect, fir_abs);

            if (errors == 0 && viol_en == 0 && viol_rst == 0 && rate_bad == 0 &&
                fir_fail == 0 && checks > 0)
                $display(" RESULT: PASS");
            else
                $display(" RESULT: FAIL");

            $display("==================================================================");
            $display("");
        end
    endtask

    // --------------------------------------------------- stimulus sequence --
    initial begin
        $display("");
        $display("==================================================================");
        $display(" tb_amc1306_sample : AMC1306M25, +/-250 mV FS, 256 MHz -> 16 MHz");
        $display(" sinc3 OSR=%0d -> %0.1f kSPS, %0d segments x %0d bits",
                 OSR, 16000.0/OSR, NUM_SEG, SEG_BITS);
        $display("==================================================================");

        rst_n  = 1'b0;
        en     = 1'b0;
        repeat (20) @(posedge clk);         // clkin is frozen while in reset
        @(negedge clk); rst_n = 1'b1;       // both counters restart at 0 -> aligned
        repeat (8) @(posedge clk);
        @(negedge clk); en = 1'b1;

        // ---- one full pass over every level (bit_cnt wraps back to seg 0) --
        repeat (BIT_DEPTH) @(posedge clkin);

        // ---- en gating: stream is back on segment 0 (-250 mV) --------------
        rate_en = 1'b0;                     // the en test must not skew s_vc
        repeat (SETTLE*OSR + 4*OSR) @(posedge clkin);
        @(negedge clk); en = 1'b0;
        repeat (EN_OFF) @(posedge clkin);
        @(negedge clk); en = 1'b1;
        resume_skip = SETTLE;               // written on the falling edge: no race
        repeat ((SETTLE+2)*OSR) @(posedge clkin);

        // ---- 51-tap FIR needs 51 samples to settle: freeze the stream on
        // ---- segment 0 (-250 mV) and look at the filtered output
        @(negedge clk); tail_mode = 1'b1;
        repeat (70*OSR) @(posedge clkin);
        fir_expect = (seg_expected[0] * EXP_NUM) / EXP_DEN;
        fir_err    = post_last - fir_expect;
        if (fir_err < 0) fir_abs = -fir_err; else fir_abs = fir_err;
        if (fir_abs > TOL_LSB) fir_fail = 1;
        if (post_n <= 0)       fir_fail = 1;

        report();
        $finish;
    end

    // ---------------------------------------------------------- watchdog ----
    initial begin
        repeat (BIT_DEPTH*DIV*3) @(posedge clk);   // ~3x the normal run length
        $display("ERROR: watchdog timeout, simulation hung");
        $finish;
    end

`ifdef DUMP_VCD
    initial begin
        $dumpfile("tb_amc1306_sample.vcd");
        $dumpvars(0, tb_amc1306_sample);
    end
`endif

endmodule
