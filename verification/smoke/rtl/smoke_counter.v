// smoke_counter -- verification-harness bring-up stand-in, NOT the Toaster
// timer. Deliberately trivial: an 8-bit up-counter with enable and async
// active-low reset. It exists so the cocotb + Icarus path is exercised end
// to end before any real RTL depends on it.
module smoke_counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       enable,
    output reg  [7:0] count
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 8'd0;
        else if (enable)
            count <= count + 8'd1;
    end

endmodule
