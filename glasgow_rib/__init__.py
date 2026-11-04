from collections.abc import Buffer
import os
import time
import asyncio
from amaranth import *
from amaranth.lib import io, wiring, stream
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.wiring import In, Out

from glasgow.support import logging
from glasgow.support.logging import dump_hex
from glasgow.support.endpoint import ServerEndpoint
from glasgow.abstract import AbstractAssembly, GlasgowPin
from glasgow.applet import GlasgowAppletV2, GlasgowAppletError


__all__ = ["RIBComponent", "RIBInterface", "RIBApplet"]


BUSY_MODES = ("none", "manual", "auto")

# A burst stays open this many bit times after the transmit FIFO runs dry, so bytes that trickle in
# from a pty or socket a little late still join the same burst instead of waiting for a new idle.
# Kept below the 10-bit idle that other nodes wait for.
BURST_GAP_BITS = 3


class RIBComponent(wiring.Component):
    """Motorola RIB (Radio Interface Box) PHY.

    Drives the radio's single-wire BUS+ line (SB9600/SBEP) as open-drain, 8N1. Everything seen on
    the line, including our own bytes, goes back to the host, the same way a real RIB echoes every
    byte to RSS; RSS relies on that echo for its collision checking. Like a real RIB, the PHY does
    no collision handling of its own: it transmits the whole frame, and the host compares the echo
    to spot a collision.

    A transmit burst only starts once the line has been idle (and BUSY, if present, inactive) for
    one packet time (10 bit times). Bytes queued back to back are then sent without gaps, the way
    a real RIB sends a message. A burst stays open for ``BURST_GAP_BITS`` bit times after the
    transmit FIFO runs dry, so a frame that reaches the FIFO in pieces (from a pty or socket) still
    goes out as one burst.

    The optional BUSY line (active low and open-drain by default; active high drives high and
    relies on a pull-down) is driven
    either from a register ("manual") or automatically for the duration of each transmit burst plus
    ``busy_hold`` bit times ("auto"), with the first start bit ``busy_setup`` bit times
    after BUSY is asserted. Its input is glitch filtered over 1/8 of a bit time.
    GM600-class radios have no BUSY line; SB9600 radios such as the GM300 do.

    ``busy_mode`` resets to ``"auto"`` when a BUSY pin is present, and to ``"none"`` otherwise.
    """

    def __init__(self, ports, *, busy_active_high=False):
        self.ports = ports
        self.busy_active_high = busy_active_high

        busy_mode_init = BUSY_MODES.index("auto" if ports.busy is not None else "none")
        super().__init__({
            "i_stream":    In(stream.Signature(8)),
            "o_stream":    Out(stream.Signature(8)),

            "bit_cyc":     In(16),
            "busy_mode":   In(2, init=busy_mode_init),
            "busy_set":    In(1),
            "busy_setup":  In(8),
            "busy_hold":   In(8),
            "busy_in":     Out(1),

            "rx_errors":   Out(16),
            "rx_overflow": Out(16),
        })

    def elaborate(self, platform):
        m = Module()

        # Open-drain BUS+: only ever pull low, read back the wired-AND of everyone on the bus.
        tx_o = Signal(init=1)
        rx_i = Signal(init=1)
        m.submodules.bus_buffer = bus_buffer = io.Buffer("io", self.ports.bus)
        m.d.comb += [
            bus_buffer.o.eq(0),
            bus_buffer.oe.eq(~tx_o),
        ]
        m.submodules += FFSynchronizer(bus_buffer.i, rx_i, init=1)

        busy_o = Signal()
        if self.ports.busy is not None:
            busy_i = Signal(init=1)
            m.submodules.busy_buffer = busy_buffer = io.Buffer("io", self.ports.busy)
            # Only ever drive the asserted level; the pull (set by the interface) idles the line.
            m.d.comb += [
                busy_buffer.o.eq(self.busy_active_high),
                busy_buffer.oe.eq(busy_o),
            ]
            m.submodules += FFSynchronizer(busy_buffer.i, busy_i,
                init=int(not self.busy_active_high))

            # Glitch filter: only accept a new BUSY level after it was stable for bit_cyc/8.
            busy_raw  = Signal()
            busy_cnt  = Signal(13)
            m.d.comb += busy_raw.eq(busy_i if self.busy_active_high else ~busy_i)
            with m.If(busy_raw == self.busy_in):
                m.d.sync += busy_cnt.eq(self.bit_cyc[3:])
            with m.Elif(busy_cnt == 0):
                m.d.sync += self.busy_in.eq(busy_raw)
            with m.Else():
                m.d.sync += busy_cnt.eq(busy_cnt - 1)

        # Free-running bit time tick, used for idle detection and BUSY hold-off.
        tick_timer = Signal(16)
        tick = Signal()
        with m.If(tick_timer == 0):
            m.d.sync += tick_timer.eq(self.bit_cyc - 1)
            m.d.comb += tick.eq(1)
        with m.Else():
            m.d.sync += tick_timer.eq(tick_timer - 1)

        # Receiver
        rx_timer = Signal(16)
        rx_bitno = Signal(range(8))
        rx_shreg = Signal(8)
        rx_idle  = Signal()

        with m.If(self.o_stream.ready):
            m.d.sync += self.o_stream.valid.eq(0)

        with m.FSM(name="rx"):
            with m.State("IDLE"):
                m.d.comb += rx_idle.eq(1)
                with m.If(~rx_i):
                    m.d.sync += rx_timer.eq(self.bit_cyc[1:])
                    m.next = "START"

            with m.State("START"):
                with m.If(rx_timer == 0):
                    with m.If(~rx_i):
                        m.d.sync += [
                            rx_timer.eq(self.bit_cyc - 1),
                            rx_bitno.eq(0),
                        ]
                        m.next = "DATA"
                    with m.Else():
                        m.next = "IDLE" # glitch
                with m.Else():
                    m.d.sync += rx_timer.eq(rx_timer - 1)

            with m.State("DATA"):
                with m.If(rx_timer == 0):
                    m.d.sync += [
                        rx_shreg.eq(Cat(rx_shreg[1:], rx_i)),
                        rx_timer.eq(self.bit_cyc - 1),
                        rx_bitno.eq(rx_bitno + 1),
                    ]
                    with m.If(rx_bitno == 7):
                        m.next = "STOP"
                with m.Else():
                    m.d.sync += rx_timer.eq(rx_timer - 1)

            with m.State("STOP"):
                with m.If(rx_timer == 0):
                    with m.If(rx_i):
                        with m.If(self.o_stream.valid & ~self.o_stream.ready):
                            m.d.sync += self.rx_overflow.eq(self.rx_overflow + 1)
                        m.d.sync += [
                            self.o_stream.payload.eq(rx_shreg),
                            self.o_stream.valid.eq(1),
                        ]
                        m.next = "IDLE"
                    with m.Else():
                        m.d.sync += self.rx_errors.eq(self.rx_errors + 1)
                        m.next = "BREAK"
                with m.Else():
                    m.d.sync += rx_timer.eq(rx_timer - 1)

            with m.State("BREAK"):
                with m.If(rx_i):
                    m.next = "IDLE"

        # Bus idle detection: one packet time (10 bit times) with the line high and BUSY inactive.
        idle_bits = Signal(range(11))
        bus_idle  = Signal()
        with m.If(~rx_idle | ~rx_i | (self.busy_in & ~busy_o)):
            m.d.sync += idle_bits.eq(0)
        with m.Elif(tick & (idle_bits != 10)):
            m.d.sync += idle_bits.eq(idle_bits + 1)
        m.d.comb += bus_idle.eq(idle_bits == 10)

        # Transmitter
        tx_timer = Signal(16)
        tx_bitno = Signal(range(8))
        tx_shreg = Signal(8)
        in_burst = Signal()
        gap_bits   = Signal(range(BURST_GAP_BITS + 1))
        hold_bits  = Signal(8)
        setup_bits = Signal(8)

        with m.If(tick & (hold_bits != 0)):
            m.d.sync += hold_bits.eq(hold_bits - 1)

        with m.FSM(name="tx"):
            with m.State("IDLE"):
                with m.If(self.i_stream.valid & in_burst):
                    m.d.comb += self.i_stream.ready.eq(1)
                    m.d.sync += [
                        tx_shreg.eq(self.i_stream.payload),
                        tx_o.eq(0),
                        tx_timer.eq(self.bit_cyc - 1),
                        tx_bitno.eq(0),
                        gap_bits.eq(0),
                    ]
                    m.next = "START"
                with m.Elif(self.i_stream.valid & bus_idle):
                    # Claim the bus (asserting BUSY in auto mode), then wait out the setup time.
                    m.d.sync += in_burst.eq(1)
                    with m.If(self.busy_mode == BUSY_MODES.index("auto")):
                        m.d.sync += setup_bits.eq(self.busy_setup)
                    with m.Else():
                        m.d.sync += setup_bits.eq(0)
                    m.next = "SETUP"
                with m.Elif(in_burst & ~self.i_stream.valid):
                    # Keep the burst open briefly in case the rest of the frame is still on its way.
                    with m.If(gap_bits >= BURST_GAP_BITS):
                        m.d.sync += [
                            in_burst.eq(0),
                            gap_bits.eq(0),
                            hold_bits.eq(self.busy_hold),
                        ]
                    with m.Elif(tick):
                        m.d.sync += gap_bits.eq(gap_bits + 1)

            with m.State("SETUP"):
                with m.If(setup_bits == 0):
                    m.next = "IDLE"
                with m.Elif(tick):
                    m.d.sync += setup_bits.eq(setup_bits - 1)

            with m.State("START"):
                with m.If(tx_timer == 0):
                    m.d.sync += [
                        tx_o.eq(tx_shreg[0]),
                        tx_shreg.eq(tx_shreg[1:]),
                        tx_timer.eq(self.bit_cyc - 1),
                    ]
                    m.next = "DATA"
                with m.Else():
                    m.d.sync += tx_timer.eq(tx_timer - 1)

            with m.State("DATA"):
                with m.If(tx_timer == 0):
                    m.d.sync += [
                        tx_o.eq(tx_shreg[0]),
                        tx_shreg.eq(tx_shreg[1:]),
                        tx_timer.eq(self.bit_cyc - 1),
                        tx_bitno.eq(tx_bitno + 1),
                    ]
                    with m.If(tx_bitno == 7):
                        m.d.sync += tx_o.eq(1)
                        m.next = "STOP"
                with m.Else():
                    m.d.sync += tx_timer.eq(tx_timer - 1)

            with m.State("STOP"):
                with m.If(tx_timer == 0):
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += tx_timer.eq(tx_timer - 1)

        with m.Switch(self.busy_mode):
            with m.Case(BUSY_MODES.index("manual")):
                m.d.comb += busy_o.eq(self.busy_set)
            with m.Case(BUSY_MODES.index("auto")):
                m.d.comb += busy_o.eq(in_burst | (hold_bits != 0))

        return m


class RIBInterface:
    def __init__(self, logger: logging.Logger, assembly: AbstractAssembly, *,
                 bus: GlasgowPin, busy: GlasgowPin | None, busy_active_high=False):
        self._logger = logger
        self._level  = logging.DEBUG if self._logger.name == __name__ else logging.TRACE

        ports = assembly.add_port_group(bus=bus, busy=busy)
        assembly.use_pulls({bus: "high"})
        if busy is not None:
            assembly.use_pulls({busy: "low" if busy_active_high else "high"})
        component = assembly.add_submodule(
            RIBComponent(ports, busy_active_high=busy_active_high))
        # No explicit IN flush: the FX2 sends a short packet 125 us after the last byte, which at
        # 9600 baud means every received byte reaches RSS about as fast as USB allows.
        self._pipe = assembly.add_inout_pipe(component.o_stream, component.i_stream)
        self._bit_cyc     = assembly.add_rw_register(component.bit_cyc)
        self._busy_mode   = assembly.add_rw_register(component.busy_mode)
        self._busy_set    = assembly.add_rw_register(component.busy_set)
        self._busy_setup  = assembly.add_rw_register(component.busy_setup)
        self._busy_hold   = assembly.add_rw_register(component.busy_hold)
        self._busy_in     = assembly.add_ro_register(component.busy_in)
        self._rx_errors   = assembly.add_ro_register(component.rx_errors)
        self._rx_overflow = assembly.add_ro_register(component.rx_overflow)
        self._sys_clk_period = assembly.sys_clk_period

    def _log(self, message, *args):
        self._logger.log(self._level, "RIB: " + message, *args)

    async def set_baud(self, baud):
        """Configures the bus baud rate. SB9600 and SBEP both start at 9600 baud."""
        bit_cyc = round(1 / (baud * self._sys_clk_period))
        if not 4 <= bit_cyc < (1 << 16):
            raise GlasgowAppletError(f"baud rate {baud} is out of range")
        await self._bit_cyc.set(bit_cyc)

    async def set_busy_mode(self, mode, *, setup_bits=0, hold_bits=10):
        """Configures BUSY handling: ``"none"``, ``"manual"`` (see :meth:`set_busy`), or
        ``"auto"`` (asserted ``setup_bits`` bit times before each transmit burst and released
        ``hold_bits`` bit times after it).
        """
        await self._busy_setup.set(setup_bits)
        await self._busy_hold.set(hold_bits)
        await self._busy_mode.set(BUSY_MODES.index(mode))

    async def set_busy(self, active: bool):
        """Asserts or releases BUSY in ``"manual"`` mode."""
        self._log("busy=%d", active)
        await self._busy_set.set(int(active))

    async def get_busy(self) -> bool:
        """Returns whether BUSY is currently asserted on the bus (by anyone, including us)."""
        return bool(await self._busy_in)

    async def read(self, length: int, *, flush=True) -> memoryview:
        """Reads ``length`` bytes from the bus. Bytes we transmitted are included (echoed)."""
        self._log("rx len=%d", length)
        if flush:
            await self.flush()
        data = await self._pipe.recv(length)
        self._log("rx data=<%s>", dump_hex(data))
        return data

    async def read_all(self, *, flush=True) -> memoryview:
        """Reads all buffered bytes from the bus, but no less than one byte."""
        if flush:
            await self.flush()
        data = await self._pipe.recv(self._pipe.readable or 1)
        self._log("rx data=<%s>", dump_hex(data))
        return data

    async def write(self, data: Buffer, *, flush=False):
        """Queues bytes for transmission. Bytes queued together go out back to back."""
        data = memoryview(data)
        self._log("tx data=<%s>", dump_hex(data))
        await self._pipe.send(data)
        if flush:
            await self.flush()

    async def flush(self):
        await self._pipe.flush()

    async def transfer(self, data: Buffer, *, timeout: float | None = None) -> memoryview:
        """Transmits ``data`` and returns its echo as read back from the bus. A mismatch between
        the two means another device was talking at the same time (a collision).

        Like a real RIB, the PHY transmits the whole frame regardless; on a collision the echo
        comes back corrupted, so the comparison fails. With ``timeout`` (in seconds), whatever
        arrived by then is returned; without it, this waits forever.

        After a collision, the other talker's bytes are also in the receive pipe, and the next
        :meth:`read` returns them. Drain them or resync on frame boundaries (see
        ``gm600/PROTOCOL.md``, "Other traffic on BUS+") before retrying.
        """
        await self.write(data, flush=True)
        if timeout is None:
            return await self.read(len(data), flush=False)

        echo = bytearray()
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            # One byte at a time, so nothing past the echo (i.e. the reply) is consumed.
            while len(echo) < len(data):
                remaining = deadline - asyncio.get_running_loop().time()
                echo += await asyncio.wait_for(self._pipe.recv(1), max(remaining, 0))
        except TimeoutError:
            self._log("echo timeout, got %d of %d bytes", len(echo), len(data))
        self._log("rx echo=<%s>", dump_hex(echo))
        return memoryview(echo)

    async def monitor(self, *, interval=1.0):
        """Logs receive errors."""
        def check_counter(cur_value, new_value):
            delta = new_value - cur_value
            if new_value < cur_value:
                delta += 1 << 16
            return new_value, delta

        cur_errors = cur_overflow = 0
        while True:
            cur_errors, delta = check_counter(cur_errors, await self._rx_errors)
            if delta > 0:
                self._logger.warning("%d frames dropped due to framing errors", delta)

            cur_overflow, delta = check_counter(cur_overflow, await self._rx_overflow)
            if delta > 0:
                self._logger.warning("%d frames dropped due to overflow", delta)

            await asyncio.sleep(interval)


class RIBApplet(GlasgowAppletV2):
    logger = logging.getLogger(__name__)
    help = "emulate a Motorola RIB (radio interface box)"
    description = """
    Emulate a Motorola RLN4008 Radio Interface Box, connecting radio service software (RSS) to
    the SB9600/SBEP bus of radios such as the GM600, GM300 or Maxtrac.

    The bus is driven open-drain at 5 V, so no level shifter is needed. Connect BUS+ (GM600:
    accessory connector J0400 pin 6 or control head connector J0101 pin 15) and ground. On
    SB9600 radios that have a BUSY line, connect it with ``--busy``.

    Like a real RIB, every byte on the bus is passed back to the host, including the ones that
    were just transmitted, so RSS sees the echo it expects.

    Operations:

    * ``pty``: expose the bus as a pseudo-terminal, e.g. for DOSBox-X ``directserial``.
    * ``socket``: expose the bus as a TCP or UNIX socket, e.g. for DOSBox ``nullmodem``.
    * ``sniff``: never transmit; print timestamped traffic, split into messages at idle gaps.
    """

    @classmethod
    def add_build_arguments(cls, parser, access):
        access.add_voltage_argument(parser)
        access.add_pins_argument(parser, "bus", default=True, required=True)
        access.add_pins_argument(parser, "busy")
        parser.add_argument(
            "--busy-active", choices=("low", "high"), default="low",
            help="BUSY line polarity (default: %(default)s)")

    def build(self, args):
        with self.assembly.add_applet(self):
            self.assembly.use_voltage(args.voltage)
            self.rib_iface = RIBInterface(self.logger, self.assembly,
                bus=args.bus, busy=args.busy, busy_active_high=args.busy_active == "high")

    @classmethod
    def add_setup_arguments(cls, parser):
        parser.add_argument(
            "-b", "--baud", metavar="RATE", type=int, default=9600,
            help="set bus baud rate to RATE bits per second (default: %(default)s)")
        parser.add_argument(
            "--busy-mode", choices=BUSY_MODES, default="auto",
            help="BUSY line handling, if --busy is given (default: %(default)s)")
        parser.add_argument(
            "--busy-setup", metavar="BITS", type=int, default=0,
            help="in auto mode, assert BUSY BITS bit times before the first start bit "
                 "(default: %(default)s)")
        parser.add_argument(
            "--busy-hold", metavar="BITS", type=int, default=10,
            help="in auto mode, keep BUSY asserted BITS bit times after a burst "
                 "(default: %(default)s)")

    async def setup(self, args):
        await self.rib_iface.set_baud(args.baud)
        if args.busy is not None:
            await self.rib_iface.set_busy_mode(args.busy_mode,
                setup_bits=args.busy_setup, hold_bits=args.busy_hold)

    @classmethod
    def add_run_arguments(cls, parser):
        p_operation = parser.add_subparsers(dest="operation", metavar="OPERATION", required=True)

        p_operation.add_parser(
            "pty", help="connect the bus to a pseudo-terminal device file")

        p_socket = p_operation.add_parser(
            "socket", help="connect the bus to a socket")
        ServerEndpoint.add_argument(p_socket, "endpoint")

        p_sniff = p_operation.add_parser(
            "sniff", help="passively log bus traffic")
        p_sniff.add_argument(
            "--gap", metavar="MS", type=float, default=5.0,
            help="start a new line after MS milliseconds of silence (default: %(default)s)")

    async def _forward_fd(self, fileno):
        async def forward_out():
            while True:
                data = await asyncio.get_event_loop().run_in_executor(None,
                    lambda: os.read(fileno, 1024))
                if len(data) == 0:
                    raise EOFError
                await self.rib_iface.write(data, flush=True)

        async def forward_in():
            while True:
                data = await self.rib_iface.read_all(flush=False)
                await asyncio.get_event_loop().run_in_executor(None,
                    lambda data=data: os.write(fileno, data))

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.rib_iface.monitor())
                group.create_task(forward_out())
                group.create_task(forward_in())
        except* EOFError:
            pass

    async def _run_pty(self):
        import pty
        import tty

        primary, secondary = pty.openpty()
        tty.setraw(secondary)
        print(os.ttyname(secondary), flush=True)

        await self._forward_fd(primary)

    async def _run_socket(self, sock_addr):
        endpoint = await ServerEndpoint("socket", self.logger, sock_addr)

        async def forward_out():
            while True:
                try:
                    data = await endpoint.recv()
                except EOFError:
                    continue
                await self.rib_iface.write(data, flush=True)

        async def forward_in():
            while True:
                data = await self.rib_iface.read_all(flush=False)
                await endpoint.send(data)

        async with asyncio.TaskGroup() as group:
            group.create_task(self.rib_iface.monitor())
            group.create_task(forward_out())
            group.create_task(forward_in())

    async def _run_sniff(self, gap):
        # Timestamps are taken on the host when USB data arrives, so they are only accurate to
        # a few milliseconds; good enough to tell messages apart, not to measure bus timing.
        async def log_traffic():
            start = time.monotonic()
            first = None
            line  = bytearray()
            while True:
                try:
                    data = await asyncio.wait_for(self.rib_iface.read_all(flush=False),
                        timeout=gap / 1000 if line else None)
                except TimeoutError:
                    print(f"{first - start:10.3f}  {line.hex(' ')}", flush=True)
                    line.clear()
                    continue
                if not line:
                    first = time.monotonic()
                line += data

        async with asyncio.TaskGroup() as group:
            group.create_task(self.rib_iface.monitor())
            group.create_task(log_traffic())

    async def run(self, args):
        match args.operation:
            case "pty":
                await self._run_pty()
            case "socket":
                await self._run_socket(args.endpoint)
            case "sniff":
                await self._run_sniff(args.gap)

    @classmethod
    def tests(cls):
        from . import test
        return test.RIBAppletTestCase
