// ============================================================================
//  amc1306_sample  --  reference / stub implementation of the DUT
// ============================================================================
//  This file exists so the testbench runs out of the box, before the real RTL
//  is dropped in.  It implements the interface the testbench expects, with the
//  signal chain the workload describes:
//
//      clk 256 MHz --> /16 --> CLKIN 16 MHz --> AMC1306 DOUT (bit_in)
//      bit_in --> sinc3 CIC, OSR = 256 --> 62.5 kSPS --> 16-bit signed code
//
//  Wiring it into the project: keep the module name and the port names, replace
//  the body with your own RTL, or simply delete this file once your module
//  `amc1306_sample` is added to the simulation set.
//
//  Expected output coding (see ../README.md):
//      code = (ones_density - 0.5) * 65536          (two's complement, 16 bit)
//      Vin = -250 mV -> d = 10.9375 %  -> code = -25600
//      Vin =    0 mV -> d = 50      %  -> code =      0
//      Vin = +250 mV -> d = 89.0625 %  -> code = +25600
//  This is the *ideal* AMC1306M25 transfer function; if your DUT applies a
//  different gain/offset trim, adjust EXP_NUM / EXP_DEN in the testbench
//  (README section 5.3) -- do not change the DUT just to make the TB pass.
// ============================================================================

`timescale 1ps/1ps

module amc1306_sample #(
    parameter integer DIV = 16,             // 256 MHz / DIV = CLKIN = 16 MHz
    parameter integer OSR = 256             // sinc3 decimation ratio
)(
    input  wire        clk,                 // 256 MHz system clock
    input  wire        rst_n,               // asynchronous, active low
    input  wire        en,                   // 1 = sampling enabled
    input  wire        bit_in,               // AMC1306 DOUT
    output reg         valid,                // 1-clk pulse per output sample
    output reg  [15:0] data                  // corrected 16-bit signed result
);

    // ------------------------------------------------------------------ CLKIN
    // 50 % duty divide-by-DIV.  Rising edge occurs on div_cnt == 0, which is
    // also where the testbench's replica counter restarts after reset.
    localparam integer DIV_W = (DIV<=2)?1:(DIV<=4)?2:(DIV<=8)?3:(DIV<=16)?4:
                               (DIV<=32)?5:6;
    localparam integer OSR_W = (OSR<=2)?1:(OSR<=4)?2:(OSR<=8)?3:(OSR<=16)?4:
                               (OSR<=32)?5:(OSR<=64)?6:(OSR<=128)?7:8;

    reg  [DIV_W-1:0] div_cnt;
    reg  [OSR_W-1:0] dec_cnt;
    reg              clkin_d;

    wire clkin = ~div_cnt[DIV_W-1];         // requires DIV = 2**n
    wire tick  = (div_cnt == DIV-1);        // one update per CLKIN period

    always @(posedge clk or negedge rst_n)
        if (!rst_n) div_cnt <= {DIV_W{1'b0}};
        else        div_cnt <= (div_cnt == DIV-1) ? {DIV_W{1'b0}} : div_cnt + 1'b1;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) clkin_d <= 1'b0;
        else        clkin_d <= clkin;

    // DOUT is launched by the CLKIN rising edge and is valid 15 ns later, with
    // 3.5 ns hold.  Sampling on the CLKIN falling edge (half a period later)
    // is the safe point, exactly like the datasheet timing diagram.
    wire samp = clkin_d & ~clkin;

    reg bit_s;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) bit_s <= 1'b0;
        else if (samp) bit_s <= bit_in;

    wire signed [31:0] b = {{31{1'b0}}, bit_s};

    // ------------------------------------------------- sinc3 (CIC) decimator
    // The integrators are deliberately allowed to wrap: a CIC only needs the
    // *differences* to be correct, and every stage here is exact modulo 2**32.
    // Only the third difference (d3) has to fit, and it is bounded by
    // OSR**3 = 2**24 for a density of 1.0.  Do not "fix" the wraparound.
    reg  signed [31:0] i1, i2, i3;          // integrators, 16 MHz
    reg  signed [31:0] z1, z2, z3;          // comb delays, 62.5 kHz

    wire signed [31:0] i1_n = i1 + b;
    wire signed [31:0] i2_n = i2 + i1 + b;
    wire signed [31:0] i3_n = i3 + i2 + i1 + b;

    wire signed [31:0] d1 = i3_n - z1;
    wire signed [31:0] d2 = d1   - z2;
    wire signed [31:0] d3 = d2   - z3;

    wire dec_tick = tick && (dec_cnt == OSR-1);

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            i1 <= 32'sd0; i2 <= 32'sd0; i3 <= 32'sd0;
            z1 <= 32'sd0; z2 <= 32'sd0; z3 <= 32'sd0;
            dec_cnt <= {OSR_W{1'b0}};
        end
        else if (en && tick) begin
            i1 <= i1_n;
            i2 <= i2_n;
            i3 <= i3_n;
            if (dec_cnt == OSR-1) begin
                z1      <= i3_n;
                z2      <= d1;
                z3      <= d2;
                dec_cnt <= {OSR_W{1'b0}};
            end
            else dec_cnt <= dec_cnt + 1'b1;
        end

    // ------------------------------------------- scale to 16-bit signed code
    // CIC DC gain is OSR**3 = 2**24 for a density of 1.0, so >>8 gives
    // density * 2**16; subtracting 2**15 centres the result on 0 A.
    wire signed [31:0] scaled = (d3 >>> 8) - 32'sd32768;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            valid <= 1'b0;
            data  <= 16'sd0;
        end
        else if (!en) begin
            valid <= 1'b0;                  // no samples while disabled
        end
        else if (dec_tick) begin
            valid <= 1'b1;
            if      (scaled >  32'sd32767) data <= 16'sh7FFF;
            else if (scaled < -32'sd32768) data <= 16'sh8000;
            else                           data <= scaled[15:0];
        end
        else valid <= 1'b0;

`ifndef SYNTHESIS
    initial begin
        if ((DIV & (DIV-1)) != 0)
            $display("[amc1306_sample] ERROR: DIV must be a power of two (got %0d)", DIV);
        if ((OSR & (OSR-1)) != 0)
            $display("[amc1306_sample] ERROR: OSR must be a power of two (got %0d)", OSR);
    end
`endif

endmodule
