#!/usr/bin/env python3
"""GM600 front-panel LED light show. Uploads led_cmd.S (a RAM command stub that writes the 0x34AE
LED shadow and calls the firmware's LED push) and drives a scripted sequence over the SCI.

Bit map (0x34AE byte): 0=amber, 1=red, 2=green status LEDs; 7=LCD+keypad backlight. See ../FIRMWARE.md.
Direct 0x17 writes to 0x34AE don't stick (the firmware overwrites + only pushes on its own events),
so this takes over via the stub. DANGER: halts the firmware — power-cycle the radio afterwards.

    python3 led_demo.py
"""
import time
import asm6811
import sbep

AMBER, RED, GREEN, BL = 0x01, 0x02, 0x04, 0x80


def main():
    a = asm6811.assemble_file("led_cmd.S")
    org, code = a.org, a.code
    with sbep.Radio() as r:
        print("serial:", r.id_string(1), "- uploading LED stub...")
        for off in range(0, len(code), 32):
            st, _, _ = r.transact(sbep.encode(0x17, (org + off).to_bytes(3, "big") + code[off:off + 32]))
            assert st == sbep.ACK, (hex(org + off), st)
        back = b"".join(r.read_block(org + o, min(64, len(code) - o)) for o in range(0, len(code), 64))
        assert back == code, "stub verify mismatch"
        r.drain(); r.sock.sendall(sbep.encode(0x26, org.to_bytes(2, "big"))); time.sleep(0.4)

        def set(v, dwell):
            for _ in range(2):
                r.sock.sendall(bytes([v])); time.sleep(0.01)
            time.sleep(dwell)

        print("light show...")
        for _ in range(3):                                   # 1. status cycle red -> amber -> green
            for c in (RED, AMBER, GREEN):
                set(c, 0.35)
        for _ in range(5):                                   # 2. knight-rider on the tri-color stack
            for c in (RED, AMBER, GREEN, AMBER):
                set(c, 0.11)
        for _ in range(4):                                   # 3. all status LEDs blink
            set(RED | AMBER | GREEN, 0.18); set(0, 0.18)
        for _ in range(4):                                   # 4. backlight blink
            set(BL, 0.3); set(0, 0.3)
        set(RED | AMBER | GREEN | BL, 1.5)                   # 5. finale: everything on
        set(0, 0.0)
    print("done - firmware halted; power-cycle the radio to restore normal operation.")


if __name__ == "__main__":
    raise SystemExit(main())
