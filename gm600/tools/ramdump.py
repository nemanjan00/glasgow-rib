"""Full-flash dumper for the GM600: upload a 68HC11 RAM stub, launch it, capture 256 KB.

The SBEP `11` read only ever sees the one flash bank mapped at `4000`-`7FFF`, and a bank-walk
can't work because the `11`/`17` handlers run from that banked window (../DUMP_SEQUENCE.md,
../FIRMWARE.md). This uploads a small stub into RAM with `17` writes, launches it with the `26`
backdoor (`sei` + `jmp`), and the stub streams the whole 256 KB flash out the SCI: each banked
page, then the fixed `8000`-`FFFF`. It does not return - you power-cycle the radio afterwards.

The stub is `ramdump.S` (readable assembly, register map as a HAL), assembled by `asm6811.py`.

    python3 ramdump.py flash.bin        # upload, launch, capture 256 KB (~4.5 min at 9600)

Prerequisites, as for every BUS+ tool: 9 V on HOOK and the applet's `socket` op on
127.0.0.1:2324 (`SBEP_ADDR` to override). See tools/README.md. DANGER: `26` runs arbitrary code
with interrupts off - only ever launch the address you just uploaded.
"""
import argparse
import os
import sys
import time

import asm6811
import sbep

STUB_S = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ramdump.S")
FLASH_SIZE = 0x40000     # 256 KB (16 x 16 KB pages)
EEPROM_ADDR, EEPROM_SIZE = 0x0D80, 0x0280   # CPU-internal EEPROM (the codeplug), 640 B


def build_stub():
    """Assemble ramdump.S. Returns (org, code)."""
    a = asm6811.assemble_file(STUB_S)
    return a.org, a.code


def upload(radio, org, code, chunk=32):
    """Write `code` into RAM at `org` with `17` blocks, then read it back and verify."""
    for off in range(0, len(code), chunk):
        part = code[off:off + chunk]
        addr = org + off
        status, _, _ = radio.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + part))
        if status != sbep.ACK:
            raise sbep.SBEPError(f"write @0x{addr:04X} not ACKed (status {status:02X})")
    back = bytearray()
    for off in range(0, len(code), 64):
        addr = org + off
        back += radio.read_block(addr, min(64, len(code) - off))
    if bytes(back) != code:
        raise sbep.SBEPError("stub read-back mismatch - RAM upload failed")


def launch_and_capture(radio, org, size, out=None, idle=3.0):
    """Launch the stub at `org` via `26` and stream `size` raw bytes.

    After the `26` frame the radio echoes it, sends one ACK (`50`), then jumps to the stub,
    which streams flash with no framing. Returns (flash_bytes, seconds)."""
    frame = sbep.encode(0x26, org.to_bytes(2, "big"))
    radio.drain()
    radio.sock.sendall(frame)
    sock = radio.sock
    sock.settimeout(1.0)
    raw = bytearray()
    start = last = time.monotonic()
    while len(raw) < len(frame) + 1 + size:
        try:
            chunk = sock.recv(65536)
        except OSError:
            chunk = b""
        if chunk:
            raw += chunk
            last = time.monotonic()
            if len(raw) % (1 << 16) < len(chunk):
                done = max(0, len(raw) - len(frame) - 1)
                sys.stderr.write(f"\r  captured {done:>7}/{size} bytes ({done * 100 // size}%)")
                sys.stderr.flush()
        elif time.monotonic() - last > idle:
            break
    sys.stderr.write("\n")
    payload = raw[len(frame):]                        # drop the echoed 26 frame
    if payload[:1] == bytes([sbep.ACK]):              # and the single launch-ACK
        payload = payload[1:]
    flash = bytes(payload[:size])
    if out:
        with open(out, "wb") as f:
            f.write(flash)
    return flash, time.monotonic() - start


def read_eeprom(radio):
    """Read the 640-byte CPU-internal EEPROM (the codeplug) with plain `11` reads."""
    data = bytearray()
    while len(data) < EEPROM_SIZE:
        data += radio.read_block(EEPROM_ADDR + len(data), min(64, EEPROM_SIZE - len(data)))
    return bytes(data)


def main(argv):
    ap = argparse.ArgumentParser(description="GM600 full-flash dumper (RAM stub via 0x26).")
    ap.add_argument("out", nargs="?", default="flash.bin", help="output file")
    ap.add_argument("--with-eeprom", action="store_true",
                    help="append the 640-byte internal EEPROM (codeplug) for a complete NV image")
    args = ap.parse_args(argv[1:])

    org, stub = build_stub()
    print(f"stub: {len(stub)} bytes at 0x{org:04X}  ({stub.hex()})")
    with sbep.Radio() as radio:
        print(f"radio: {radio.id_string(1)}")          # serial, proves it answers
        eeprom = b""
        if args.with_eeprom:
            eeprom = read_eeprom(radio)                 # read NOW; the stub halts the radio
            print(f"read internal EEPROM: {len(eeprom)} bytes")
        print("uploading stub...")
        upload(radio, org, stub)
        print(f"verified. launching - streaming {FLASH_SIZE // 1024} KB (~4.5 min at 9600)...")
        flash, secs = launch_and_capture(radio, org, FLASH_SIZE, out=None)
    short = len(flash) != FLASH_SIZE
    with open(args.out, "wb") as f:
        f.write(flash)
        f.write(eeprom)
    print(f"captured {len(flash)} flash bytes in {secs:.0f}s -> {args.out}")
    if eeprom:
        print(f"  layout: 0x00000-0x{FLASH_SIZE - 1:05X} flash, "
              f"0x{FLASH_SIZE:05X}-0x{FLASH_SIZE + len(eeprom) - 1:05X} internal EEPROM")
    if short:
        print("WARNING: short flash capture - the stub may have been reset mid-dump; "
              "power-cycle and retry.")
    print("power-cycle the radio now (the stub left it halted).")
    return 1 if short else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
