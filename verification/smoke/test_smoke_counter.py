"""cocotb smoke test for smoke_counter (harness bring-up, NOT the Toaster timer).

Proves the cocotb + Icarus path end to end: reset holds the counter at zero,
enable high makes it increment every cycle, enable low freezes it. The
repo-root `make smoke` runs this and appends the result to
verification/smoke/evidence/ as a JSON record.

Timing discipline: stimulus is driven on the falling edge (read-write phase)
so it is stable well before the next posedge, and values are sampled after
`ReadOnly()` so a counter updated by the edge's non-blocking assignment has
settled before the assert.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge


@cocotb.test()
async def counts_when_enabled(dut) -> None:
    """Reset holds zero; enable high increments every cycle; enable low freezes."""
    cocotb.start_soon(Clock(dut.clk, 10).start())

    dut.enable.value = 1
    dut.rst_n.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.count.value) == 0, (
        f"counter must hold 0 while rst_n is low, got {int(dut.count.value)}"
    )

    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    expected = 0
    for _ in range(8):
        await RisingEdge(dut.clk)
        await ReadOnly()
        expected = (expected + 1) & 0xFF
        got = int(dut.count.value)
        assert got == expected, f"after enable: got {got}, expected {expected}"

    await FallingEdge(dut.clk)
    dut.enable.value = 0
    frozen = int(dut.count.value)
    for _ in range(3):
        await RisingEdge(dut.clk)
        await ReadOnly()
        got = int(dut.count.value)
        assert got == frozen, (
            f"counter must freeze while enable is low, got {got}, expected {frozen}"
        )
