#!/usr/bin/env python3
"""Live web front panel for the REAL GM600 over SBEP: see the display/LEDs, press the keys.

The same page as the emulator UI (gm600_vm_bridge.py --ui, gm600_display.serve_ui), backed by the
radio instead of the VM:

  screen   RAM 0x3495 = the 5-byte struct [b0 b1 d0 d1 d2] the firmware last sent to the display
           controller (icons + the three 7-seg digits), decoded with gm600_display; LEDs = 0x34AE;
           receive flags 0x347F (carrier) / 0x347E (PL decoded = RX active) shown on the status line
  keys     the non-halting RAM stub keyevt.S, launched with 0x26, posts the key event through the
           firmware's own routine 0x4D8B (event number == key code, e.g. A = 0x14). A click (released
           within 0.4 s, decided by the page) is a tap: post "pressed", then "released". Holding longer pauses the keypad task (TCB flags 0x11D8 |= 0x10)
           until the mouse button is released.

Runs on the host with the rib applet (SBEP_ADDR, default 127.0.0.1:2324; single client — stop other
SBEP tools first). Open from another machine with ``ssh -L 8608:127.0.0.1:8600 <lab>`` (preferred:
the panel has no authentication), or ``--host 0.0.0.0`` on a trusted network only.

Also: Download / Preview codeplug (640-byte EEPROM read live; preview decodes the Own Unit No., the
conventional channels and the checksums) and Mode buttons (trunked/conventional via gm600_mode: two
retained-RAM bytes + 0x10 reset, ~10 s).

  gm600_live.py [--port 8600] [--host 127.0.0.1] [--poll 0.3]
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asm6811
import gm600_display
import gm600_mode
import lcd_text
import sbep

STRUCT, LEDS, RXFLAGS = 0x3495, 0x34AE, 0x347E
KEYPAD_TCB_FLAGS, PAUSE = 0x11D8, 0x10
EVT, STATE = 0x3F00, 0x3F01


class LiveRadio:
    def __init__(self, addr=None, poll=0.15):
        self.r = sbep.Radio(addr)
        self.lock = threading.Lock()
        self.poll = poll
        self.state = {"segments": [], "leds": 0, "frames": 0, "rx": "?"}
        self.held = None
        self.serial = self.r.id_string(1)
        a = asm6811.assemble_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "keyevt.S"))
        self.org, self.code = a.org, a.code
        self._upload()

    def _upload(self):
        for off in range(0, len(self.code), 32):
            self._write(self.org + off, self.code[off:off + 32])

    def _write(self, addr, data):
        st, _, _ = self.r.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + bytes(data)))
        if st != sbep.ACK:
            raise sbep.SBEPError(f"write @0x{addr:04X}: status {st:02X}")

    def _post(self, event, pressed, verify=True):
        self._write(EVT, [event, 1 if pressed else 0])
        try:
            # verify=False on a release: verified half a second earlier, and keyevt.S also checks
            # itself before touching the firmware
            sbep.launch_returning_stub(self.r, self.org, self.code if verify else None)
        except sbep.SBEPError as e:
            if "changed before launch" not in str(e):
                raise
            # the radio reset (power cycle, or a trunked/conventional mode change) and its boot
            # RAM test wiped the stub: put it back and try once more
            self._upload()
            self._write(EVT, [event, 1 if pressed else 0])
            sbep.launch_returning_stub(self.r, self.org, self.code)
        self.r.drain()
        time.sleep(0.03)                                # stub returns into the firmware

    def _keypad(self, paused):
        flags = self.r.read_block(KEYPAD_TCB_FLAGS, 1)[0]
        self._write(KEYPAD_TCB_FLAGS, [(flags | PAUSE) if paused else (flags & ~PAUSE & 0xFF)])

    def _retry(self, fn, seconds=15.0):
        """Run fn, retrying while the radio is busy resetting (e.g. after a mode change, which
        re-runs the self-test and leaves SBEP silent for a few seconds)."""
        deadline = time.time() + seconds
        while True:
            try:
                return fn()
            except (sbep.SBEPError, OSError):
                if time.time() > deadline:
                    raise
                time.sleep(1.0)
                self.r.drain()

    def press(self, code, action="tap"):
        with self.lock:
            if action == "tap":
                # press + explicit release in one request (~0.5 s apart). Leaving the release to the
                # keypad task's next scan stepped scroll keys the wrong way at random.
                self._post(code, True)
                self._post(code, False, verify=False)
            elif action == "down":
                if self.held is not None:
                    self._post(self.held, False)
                self._keypad(True)
                self._post(code, True)
                self.held = code
            elif action == "up":
                held, self.held = self.held, None
                if held is not None:
                    self._retry(lambda: self._post(held, False))
                # always leave the keypad task running (a reset also clears the pause by itself)
                self._retry(lambda: self._keypad(False))

    def _read_state(self):
        with self.lock:
            struct = self.r.read_block(STRUCT, 5)
            leds = self.r.read_block(LEDS, 1)[0]
            pl, carrier = self.r.read_block(RXFLAGS, 2)
        ctrl = gm600_display.Controller()
        ctrl.feed(bytes(5) + lcd_text.pack(struct))
        rx = "RX (PL decoded)" if pl else ("carrier, no PL" if carrier else "idle")
        return {"segments": sorted(ctrl.segments), "leds": leds,
                "frames": self.state["frames"] + 1, "rx": rx}

    def poll_forever(self):
        while True:
            try:
                self.state = self._read_state()
            except (sbep.SBEPError, OSError) as e:
                self.state = dict(self.state, rx=f"link error: {e}")
                time.sleep(1.0)
            time.sleep(self.poll)

    def codeplug(self):
        """The 640-byte EEPROM codeplug (0x0D80-0x0FFF), read live."""
        with self.lock:
            return b"".join(self.r.read_block(a, 0x20) for a in range(0x0D80, 0x1000, 0x20))

    def set_mode(self, to):
        """Trunked/conventional via the two retained-RAM bytes + reset (gm600_mode)."""
        with self.lock:
            return gm600_mode.set_mode(self.r, to)

    def close(self):
        try:
            self.press(None, "up")
        except (sbep.SBEPError, OSError) as e:
            print(f"warning: could not release/resume the keypad cleanly: {e}", file=sys.stderr)
        self.r.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1). The panel has NO authentication "
                         "and can press keys (incl. on/off) and switch modes: use an ssh tunnel "
                         "rather than 0.0.0.0 unless the network is trusted")
    ap.add_argument("--poll", type=float, default=0.15, help="seconds between screen reads")
    args = ap.parse_args()
    radio = LiveRadio(poll=args.poll)
    threading.Thread(target=radio.poll_forever, daemon=True).start()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: listening on {args.host} with NO authentication - anyone who can reach it can "
              "press keys, switch modes and read the codeplug. Prefer the default + an ssh tunnel.",
              file=sys.stderr)
    port = gm600_display.serve_ui(lambda: radio.state, args.port, press=radio.press, host=args.host,
                                  codeplug=radio.codeplug, set_mode=radio.set_mode)
    print(f"GM600 {radio.serial} live panel: http://{args.host}:{port}/  (Ctrl-C to stop)", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        radio.close()


if __name__ == "__main__":
    sys.exit(main())
