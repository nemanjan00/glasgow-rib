from amaranth import *

from glasgow.simulation.assembly import SimulationAssembly
from glasgow.applet import GlasgowAppletV2TestCase, synthesis_test, applet_v2_simulation_test
from . import RIBApplet


# Simulation runs at 1 MHz; a faster baud keeps the tests quick without changing the logic.
BAUD    = 100_000
BIT_CYC = round(1e6 / BAUD)


class RIBAppletTestCase(GlasgowAppletV2TestCase, applet=RIBApplet):
    @synthesis_test
    def test_build(self):
        self.assertBuilds()

    @synthesis_test
    def test_build_busy(self):
        self.assertBuilds(["--busy", "A1"])

    def prepare_radio(self, assembly: SimulationAssembly):
        """Models the radio end of the bus: BUS+ (and BUSY, if used) are wired-AND with pull-ups,
        and the "radio" can drive either low."""
        self.radio_bus  = Signal()
        self.radio_busy = Signal()
        self.tx_edges   = []    # cycles at which the applet changed its BUS+ output
        self.busy_seen  = False # whether the applet ever asserted BUSY

        bus = assembly.get_pin("A0")
        m = Module()
        m.d.comb += bus.i.eq(~(bus.oe | self.radio_bus))
        busy = assembly._pins.get("A1")
        if busy is not None:
            m.d.comb += busy.i.eq(~(busy.oe | self.radio_busy))
        assembly.add_submodule(m)

        async def watch(ctx):
            cycle = 0
            prev_oe = 0
            async for _ in ctx.tick():
                cycle += 1
                oe = ctx.get(bus.oe)
                if oe != prev_oe:
                    self.tx_edges.append(cycle)
                    prev_oe = oe
                if busy is not None and ctx.get(busy.oe):
                    self.busy_seen = True
        assembly.add_testbench(watch, background=True)

        self.radio_queue = []
        async def radio(ctx):
            while True:
                if self.radio_queue:
                    byte = self.radio_queue.pop(0)
                    for bit in [0, *((byte >> n) & 1 for n in range(8)), 1]:
                        ctx.set(self.radio_bus, not bit)
                        await ctx.tick().repeat(BIT_CYC)
                else:
                    await ctx.tick()
        assembly.add_testbench(radio, background=True)

    @applet_v2_simulation_test(prepare=prepare_radio, args=f"--baud {BAUD}")
    async def test_echo(self, applet, ctx):
        message = bytes([0x05, 0x01, 0x00, 0x1f, 0x9a])
        self.assertEqual(await applet.rib_iface.transfer(message), message)

    @applet_v2_simulation_test(prepare=prepare_radio, args=f"--baud {BAUD}")
    async def test_burst_has_no_gaps(self, applet, ctx):
        message = bytes([0x05, 0x01, 0x00, 0x1f, 0x9a])
        await applet.rib_iface.transfer(message)
        # First falling edge is the first start bit; the last stop bit begins at the last rising
        # edge. Back-to-back 8N1 frames put that within 50 bit times of the start.
        span = self.tx_edges[-1] - self.tx_edges[0]
        self.assertLessEqual(span, 50 * BIT_CYC)

    @applet_v2_simulation_test(prepare=prepare_radio, args=f"--baud {BAUD}")
    async def test_radio_reply(self, applet, ctx):
        request = bytes([0x05, 0x01, 0x00, 0x1f, 0x9a])
        reply   = bytes([0x05, 0x81, 0x55, 0xaa, 0x3c])
        self.assertEqual(await applet.rib_iface.transfer(request), request)
        await ctx.tick().repeat(3 * 10 * BIT_CYC)
        self.radio_queue.extend(reply)
        self.assertEqual(await applet.rib_iface.read(5), reply)

    @applet_v2_simulation_test(prepare=prepare_radio, args=f"--baud {BAUD}")
    async def test_waits_for_idle_bus(self, applet, ctx):
        radio = bytes([0x11, 0x22, 0x33])
        # Queue our byte while the radio is in the middle of its first frame.
        await ctx.tick().repeat(20 * BIT_CYC)
        self.radio_queue.extend(radio)
        await ctx.tick().repeat(5 * BIT_CYC)
        await applet.rib_iface.write(b"\x44")
        self.assertEqual(await applet.rib_iface.read(4), radio + b"\x44")

    @applet_v2_simulation_test(prepare=prepare_radio, args=f"--busy A1 --baud {BAUD}")
    async def test_auto_busy(self, applet, ctx):
        message = bytes([0x05, 0x01, 0x00, 0x1f, 0x9a])
        self.assertEqual(await applet.rib_iface.transfer(message), message)
        self.assertTrue(self.busy_seen)

    @applet_v2_simulation_test(prepare=prepare_radio, args=f"--busy A1 --baud {BAUD}")
    async def test_defers_to_external_busy(self, applet, ctx):
        ctx.set(self.radio_busy, 1)
        await applet.rib_iface.write(b"\x44")
        await ctx.tick().repeat(30 * 10 * BIT_CYC)
        self.assertEqual(self.tx_edges, [])
        self.assertTrue(await applet.rib_iface.get_busy())
        ctx.set(self.radio_busy, 0)
        self.assertEqual(await applet.rib_iface.read(1), b"\x44")
