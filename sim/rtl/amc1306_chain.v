// ============================================================================
//  amc1306_chain  --  AMC1306 sampling module + 51-tap low-pass FIR
// ============================================================================
//  This is the block to drop into the real project: the AMC1306 module output
//  feeds the FIR directly, and both the raw and the filtered stream come out
//  so the effect of the filter can be observed (and probed on hardware).
//
//      clk 256 MHz --+--> amc1306_sample --> valid_pre / data_pre
//                    |                         |
//                    |                         v
//                    +--> fir_lpf        --> valid_post / data_post
//
//  Swap `fir_lpf` for the Xilinx FIR Compiler IP in the real build; the ports
//  were chosen to match the IP's AXI-stream-lite equivalent (one valid, one
//  data bus, 1-clock latency).
// ============================================================================

`timescale 1ps/1ps

module amc1306_chain #(
    parameter integer DIV = 16,
    parameter integer OSR = 256
)(
    input  wire                     clk,        // 256 MHz
    input  wire                     rst_n,
    input  wire                     en,         // 1 = sampling enabled
    input  wire                     bit_in,     // AMC1306 DOUT

    output wire                     valid_pre,  // raw AMC1306 samples
    output wire signed [15:0]       data_pre,

    output wire                     valid_post, // after the low-pass FIR
    output wire signed [15:0]       data_post
);

    amc1306_sample #(
        .DIV (DIV),
        .OSR (OSR)
    ) u_sample (
        .clk    (clk),
        .rst_n  (rst_n),
        .en     (en),
        .bit_in (bit_in),
        .valid  (valid_pre),
        .data   (data_pre)
    );

    fir_lpf u_fir (
        .clk       (clk),
        .rst_n     (rst_n),
        .valid_in  (valid_pre),
        .din       (data_pre),
        .valid_out (valid_post),
        .dout      (data_post)
    );

endmodule
