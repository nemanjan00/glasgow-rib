"""Cycle-level tests of RIBComponent, driving its transmit FIFO and the bus directly.

The applet-level tests in ``test.py`` go through the host pipe, whose latency isn't controllable;
these cover the timing-sensitive cases (bursts split across writes, late bytes after a collision)
and the corners of BUSY and the receiver.
"""
import unittest
from types import SimpleNamespace

from amaranth import *
from amaranth.lib import io
from amaranth.sim import Simulator

from . import RIBComponent, BUSY_MODES, BURST_GAP_BITS


BIT_CYC = 10   # bit time in clock cycles


class _Bench(Elaboratable):
    """The component, with BUS+ (and BUSY) wired-AND against a "radio" that can pull them low."""
    def __init__(self, *, busy=False, busy_active_high=False):
        self.bus_port  = io.SimulationPort("io", 1)
        self.busy_port = io.SimulationPort("io", 1) if busy else None
        self.dut = RIBComponent(SimpleNamespace(bus=self.bus_port, busy=self.busy_port),
                                busy_active_high=busy_active_high)
        self.radio_bus  = Signal()
        self.radio_busy = Signal()
        self.busy_active_high = busy_active_high

    def elaborate(self, platform):
        m = Module()
        m.submodules.dut = self.dut
        m.d.comb += self.bus_port.i.eq(~((self.bus_port.oe & ~self.bus_port.o) | self.radio_bus))
        if self.busy_port is not None:
            driven = self.busy_port.oe & (self.busy_port.o == int(self.busy_active_high))
            asserted = driven | self.radio_busy
            m.d.comb += self.busy_port.i.eq(asserted if self.busy_active_high else ~asserted)
        return m


class RIBComponentTestCase(unittest.TestCase):
    def run_bench(self, bench, testbench):
        sim = Simulator(bench)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.run()

    @staticmethod
    async def setup(ctx, bench, *, busy_mode=None):
        ctx.set(bench.dut.bit_cyc, BIT_CYC)
        ctx.set(bench.dut.o_stream.ready, 1)
        if busy_mode is not None:
            ctx.set(bench.dut.busy_mode, BUSY_MODES.index(busy_mode))
        # Let the bus go idle (10 bit times) first.
        await ctx.tick().repeat(12 * BIT_CYC)

    @staticmethod
    async def push(ctx, bench, byte):
        """Hands one byte to the transmit FIFO and waits until it's taken."""
        ctx.set(bench.dut.i_stream.payload, byte)
        ctx.set(bench.dut.i_stream.valid, 1)
        await ctx.tick().until(bench.dut.i_stream.ready)
        ctx.set(bench.dut.i_stream.valid, 0)

    @staticmethod
    async def wait_tx_done(ctx, bench):
        """Waits until the bus has been released for a full byte time."""
        quiet = 0
        while quiet < 10 * BIT_CYC:
            await ctx.tick()
            quiet = 0 if ctx.get(bench.bus_port.oe) else quiet + 1

    def edges_recorder(self, bench, edges):
        async def watch(ctx):
            prev = 0
            cycle = 0
            async for _ in ctx.tick():
                cycle += 1
                oe = ctx.get(bench.bus_port.oe)
                if oe != prev:
                    edges.append((cycle, oe))
                    prev = oe
        return watch

    def test_busy_mode_reset_value(self):
        for busy, mode in ((None, "none"), (object(), "auto")):
            dut = RIBComponent(SimpleNamespace(bus=None, busy=busy))
            dut._MustUse__silence = True    # never elaborated here
            self.assertEqual(dut.busy_mode.init, BUSY_MODES.index(mode))

    def _gap_between_two_bytes(self, delay_bits):
        """Sends one byte, hands the next to the FIFO ``delay_bits`` bit times after the first has
        fully gone out, and returns the idle time on the wire between the two, in bit times."""
        bench = _Bench()
        edges = []
        result = {}

        async def testbench(ctx):
            await self.setup(ctx, bench)
            await self.push(ctx, bench, 0x00)
            await ctx.tick().until(bench.bus_port.oe)     # start bit
            await ctx.tick().until(~bench.bus_port.oe)    # stop bit begins (all-zero byte)
            await ctx.tick().repeat(BIT_CYC)              # stop bit
            await ctx.tick().repeat(delay_bits * BIT_CYC)
            await self.push(ctx, bench, 0x00)
            await ctx.tick().repeat(2 * BIT_CYC)

        sim = Simulator(bench)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.add_testbench(self.edges_recorder(bench, edges), background=True)
        sim.run()
        rises = [c for c, oe in edges if oe]
        falls = [c for c, oe in edges if not oe]
        # The idle gap runs from the end of the first stop bit to the second start bit.
        return (rises[1] - (falls[0] + BIT_CYC)) / BIT_CYC

    def test_trickled_byte_joins_burst(self):
        # The second byte reaches the FIFO one bit time after the first one ended: within the
        # continuation window, so it follows without waiting for a new 10-bit idle.
        self.assertLess(self._gap_between_two_bytes(1), 3)

    def test_separate_writes_wait_for_idle(self):
        # Well past the continuation window, the next byte is a new burst: 10 idle bit times first.
        self.assertGreaterEqual(self._gap_between_two_bytes(BURST_GAP_BITS + 3), 10)

    def test_late_bytes_after_collision_are_discarded(self):
        bench = _Bench()
        result = {}

        async def testbench(ctx):
            await self.setup(ctx, bench)
            await self.push(ctx, bench, 0xff)
            await ctx.tick().until(bench.bus_port.oe)      # start bit
            await ctx.tick().repeat(BIT_CYC)
            ctx.set(bench.radio_bus, 1)                    # someone holds the line over our 1s
            await ctx.tick().repeat(2 * BIT_CYC)
            ctx.set(bench.radio_bus, 0)
            self.assertEqual(ctx.get(bench.dut.collisions), 1)
            # The tail of the aborted frame trickles in a bit time later (as it would from a pty or
            # socket, up to a byte time later): it must not be sent.
            await ctx.tick().repeat(BIT_CYC)
            await self.push(ctx, bench, 0x00)
            sent = False
            for _ in range(30 * BIT_CYC):
                await ctx.tick()
                sent |= bool(ctx.get(bench.bus_port.oe))
            result["late_sent"] = sent
            # A frame queued after the discard window goes out normally.
            await self.push(ctx, bench, 0x00)
            for _ in range(30 * BIT_CYC):
                await ctx.tick()
                if ctx.get(bench.bus_port.oe):
                    result["next_sent"] = True
                    break

        self.run_bench(bench, testbench)
        self.assertFalse(result["late_sent"])
        self.assertTrue(result.get("next_sent"))

    def test_manual_busy(self):
        bench = _Bench(busy=True)
        seen = []

        async def testbench(ctx):
            await self.setup(ctx, bench, busy_mode="manual")
            seen.append(ctx.get(bench.busy_port.oe))
            ctx.set(bench.dut.busy_set, 1)
            await ctx.tick().repeat(2)
            seen.append(ctx.get(bench.busy_port.oe))
            ctx.set(bench.dut.busy_set, 0)
            await ctx.tick().repeat(2)
            seen.append(ctx.get(bench.busy_port.oe))

        self.run_bench(bench, testbench)
        self.assertEqual(seen, [0, 1, 0])

    def test_busy_active_high(self):
        bench = _Bench(busy=True, busy_active_high=True)
        result = {}

        async def testbench(ctx):
            await self.setup(ctx, bench, busy_mode="auto")
            self.assertEqual(ctx.get(bench.busy_port.oe), 0)
            await self.push(ctx, bench, 0x55)
            await ctx.tick().until(bench.busy_port.oe)
            # Drives high, never low.
            result["o"] = ctx.get(bench.busy_port.o)
            # The radio asserting BUSY (high) is seen as busy.
            await self.wait_tx_done(ctx, bench)
            await ctx.tick().repeat(20 * BIT_CYC)
            ctx.set(bench.radio_busy, 1)
            await ctx.tick().repeat(2 * BIT_CYC)
            result["busy_in"] = ctx.get(bench.dut.busy_in)

        self.run_bench(bench, testbench)
        self.assertEqual(result["o"], 1)
        self.assertEqual(result["busy_in"], 1)

    async def _radio_send(self, ctx, bench, byte, *, stop=1):
        for bit in [0, *((byte >> n) & 1 for n in range(8)), stop]:
            ctx.set(bench.radio_bus, not bit)
            await ctx.tick().repeat(BIT_CYC)
        ctx.set(bench.radio_bus, 0)

    def test_rx_framing_error(self):
        bench = _Bench()
        result = {}

        async def testbench(ctx):
            await self.setup(ctx, bench)
            await self._radio_send(ctx, bench, 0x5a, stop=0)
            await ctx.tick().repeat(5 * BIT_CYC)
            result["errors"] = ctx.get(bench.dut.rx_errors)
            result["valid"] = ctx.get(bench.dut.o_stream.valid)

        self.run_bench(bench, testbench)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["valid"], 0)

    def test_rx_overflow(self):
        bench = _Bench()
        result = {}

        async def testbench(ctx):
            await self.setup(ctx, bench)
            ctx.set(bench.dut.o_stream.ready, 0)      # host isn't reading
            await self._radio_send(ctx, bench, 0x11)
            await self._radio_send(ctx, bench, 0x22)
            await ctx.tick().repeat(2 * BIT_CYC)
            result["overflow"] = ctx.get(bench.dut.rx_overflow)

        self.run_bench(bench, testbench)
        self.assertEqual(result["overflow"], 1)


if __name__ == "__main__":
    unittest.main()
