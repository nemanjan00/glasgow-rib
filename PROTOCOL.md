# GM600 / GP600 Programming Protocol (SBEP variant)

Host-side protocol for programming 600 Series radios (GM600 / GP600, MPT1327 trunked) over
the BUS+ line, through the `glasgow_rib` applet (`socket` or `pty` mode).

Everything here was reverse engineered from Motorola's DPS (`WDPS_DP.EXE`, R00.03.02, a 16-bit
Windows 3.1 program). Parts of it are now confirmed on a real GM600; see
[Confirmed on a GM600](#confirmed-on-a-gm600). The open questions are listed at the end.

## Physical layer

- 9600 baud, 8N1, no flow control. DPS never uses any other baud rate.
- One wire, BUS+, shared by both sides (half duplex, open drain). The GM600 has no BUSY line.
- **HOOK (J0400-14) must be at about 9 V**, or the radio never transmits on BUS+. See
  [CONNECTION.md](CONNECTION.md#hook-programming-enable).
- **Every byte you send comes back to you as an echo.** DPS reads back the echo after each
  frame and compares it with what it sent. The applet passes the echo through, so your code
  must too.

## Framing

```
byte0 = (opcode << 4) | length
```

`length` counts the data bytes **plus the checksum**, so it is `n_data + 1`. A nibble value of
`0xF` is an escape that means the real value follows:

| Case | Bytes on the wire |
|---|---|
| opcode ≤ 0xE, no data | `[op<<4]`: a single byte with no checksum (ACK = `50`, NAK = `60`) |
| opcode ≤ 0xE, 1–14 data bytes | `[op<<4 \| n+1] data… cks` |
| opcode > 0xE, 0–14 data bytes | `[F0 \| n+1] op data… cks` |
| opcode ≤ 0xE, more than 14 data bytes | `[op<<4 \| F] (n+1)>>8 (n+1)&FF data… cks` |
| opcode > 0xE, more than 14 data bytes | `FF op (n+1)_BE16 data… cks` |

- The extended opcode byte and the 16-bit length bytes are **not** counted in `length`.
- Checksum: `cks = (0xFF − sum(all preceding frame bytes)) & 0xFF`. Equivalently, the sum of
  the whole frame, checksum included, is `0xFF` mod 256.
- The receive buffer in DPS is 261 bytes (`0x105`), so a whole frame never exceeds that.
- All commands and data replies use opcodes above 0xE, so in practice you only see `F?` and
  `FF` headers, plus bare `50` (ACK) and `60` (NAK).

### Reference codec

```python
def encode(op, data=b""):
    if op <= 0xE and not data:
        return bytes([op << 4])
    n  = len(data) + 1
    lo = n if n <= 0xE else 0xF
    frame = bytearray()
    if op <= 0xE: frame.append(op << 4 | lo)
    else:         frame += bytes([0xF0 | lo, op])
    if lo == 0xF: frame += n.to_bytes(2, "big")
    frame += data
    frame.append((0xFF - sum(frame)) & 0xFF)
    return bytes(frame)


def decode(buf):
    """Returns (opcode, data, frame_len), or None if buf does not yet hold a whole frame.
    Raises ValueError on a bad checksum."""
    if not buf:
        return None
    hi, lo = buf[0] >> 4, buf[0] & 0xF
    pos = 1
    if hi == 0xF:
        if len(buf) < pos + 1: return None
        op = buf[pos]; pos += 1
    else:
        op = hi
    if lo == 0xF:
        if len(buf) < pos + 2: return None
        n = int.from_bytes(buf[pos:pos + 2], "big"); pos += 2
    else:
        n = lo
    if n == 0:
        return op, b"", pos
    end = pos + n
    if len(buf) < end:
        return None
    if sum(buf[:end]) & 0xFF != 0xFF:
        raise ValueError("bad checksum")
    return op, bytes(buf[pos:end - 1]), end


assert encode(0x10) == bytes.fromhex("F1 10 FE")
assert encode(0x23, b"\x01") == bytes.fromhex("F2 23 01 E9")
assert encode(0x11, bytes([0x20, 0, 0, 0])) == bytes.fromhex("F5 11 20 00 00 00 D9")
assert encode(0x05) == b"\x50"
```

## Transaction

1. Send the whole frame in **one** write, so the applet puts it on the wire with no gaps
   between bytes.
2. Read back `len(frame)` bytes of echo and compare them with what you sent. A mismatch
   means a collision: retry. Use a timeout here (DPS uses `(n_data + 100) * 5` ms): on a
   collision the applet stops sending and drops the rest of the frame, so fewer bytes come
   back. `iface.transfer(frame, timeout=…)` does this and returns whatever echo arrived.
3. Wait up to **200 ms** for the ACK byte `50`. `60` is a NAK.
4. If the command returns data, a second frame follows the ACK. `86` is the error opcode.

DPS uses these timings and retries:

| What | Value |
|---|---|
| Retries per command | 3 |
| ACK timeout | 200 ms |
| Longest gap allowed between received bytes | 121 ms |
| Echo readback timeout | `(n_data + 100) * 5` ms |
| Write: first ACK within | `len + 1000` ms |
| Write: result frame (`84`/`85`) within | `len * 12 + 1000` ms |
| Wait after `10` before the next command | 21 ms |
| Wait after `19` / `21` / `22` before the next command | 101 ms |

DPS's receive function returns these status codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 2 | No response from the radio |
| 3 | Incomplete response |
| 4 | Overflow |
| 5 | Bad checksum |

## Commands

| Op | What it does | Request data | Reply |
|---|---|---|---|
| `10` | Enter programming mode | none | ACK, then **the radio resets** (confirmed) |
| `13` | Ask the largest block size the radio accepts | none | ACK, then `82` frame with 3 data bytes (confirmed: `00 20 00`); which two are the size is open |
| `11` | Read a block | `len a23-16 a15-8 a7-0` (24-bit address) | ACK, then `80` frame: 3 header bytes + `len` data bytes |
| `17` | Write a block | `a23-16 a15-8 a7-0 data…` | ACK, then `84` (OK) or `85` (failed) |
| `23` | Read an ID string | `[index]`: 0, 1, 3 and 4 are used; 1 is read at connect | ACK, then `8B` frame: the index byte, then the ASCII string (confirmed for index 1: the serial number, no NUL) |
| `21` | Set a 16-bit value (address or register preset) | `00 hi lo` | ACK |
| `20` | Read or write a parameter (softpots, tuning) | read `[param, 00]`; write `[param, 01\|02, value…]` (1–3 value bytes) | read: ACK, then `8A` frame with the value; write: ACK |
| `22` | Two-byte parameter write (tuning) | `b1 b2` | ACK |
| `19` | Mode on/off | `01` / `00` | ACK; DPS never calls it |

The two write flavours of `20` (`01` and `02`) are probably RAM-only vs. permanent.

**Do not send `20`, `22` or `19` until you have captured them from real DPS traffic.** They
change the radio's alignment, and a bad write can detune the radio.

### Example frames

```
Enter programming mode     F1 10 FE
Read ID string index 1     F2 23 01 E9
Read 32 bytes at 0x000000  F5 11 20 00 00 00 D9
ACK                        50
NAK                        60
```

## Reading and writing a radio (what DPS does, captured on a GM600)

Captured on 2026-11-03: real DPS (`WDPS_DP.EXE`) under QEMU, through the applet, against a real
GM600. Logs in `~/captures/` (`sniff-readall.log`, `sniff-write.log`); not in git.

**The whole GM600 codeplug is the 640-byte CPU EEPROM at `0x0D80`–`0x0FFF`.** There is no
separate personality region, no paging and no `21`. DPS builds its `0x7F1`-byte `.per` file
from these 640 bytes.

Read:

1. `10` to enter programming mode. The radio ACKs and resets; then it answers without another `10`.
2. `23` ID queries, `13`.
3. **20 × `11` reads of 32 bytes, `0x0D80`, `0x0DA0` … `0x0FE0`.** The captured data matches a
   dump made earlier with this applet's own `11` reads byte for byte.

Write (as captured; no `19` was sent):

1. A full read as above, then `23` ×2 and `13`.
2. **`17` writes covering `0x0E13`–`0x0FFB` only**: 16 × 32 bytes from `0x0E13`, then 2 bytes at
   `0x0FF3` and 7 bytes at `0x0FF5`. `0x0D80`–`0x0E12` (serial, identity) is never written.
3. `13`, then `10` at the end (presumably to restart the radio on the new codeplug).

Each `17` is answered by an `84`/`85` result frame. Before writing, DPS checks that the radio's
serial number matches the loaded data, so a codeplug read from one radio can't be written to
another.

DPS has no "leave programming mode" command. The radio drops out on a timeout or a power cycle.

Frequency fields are big-endian 16-bit: `f = 375.000 MHz + (v & 0x7FFF) × (bit 15 ? 6.25 : 5 kHz)`.
Checked against the dump: `0x0DDA`–`0x0DE7` holds seven words decoding to 403.625, 418.625,
431.625, 439.625, 450.625, 460.625 and 469.625 MHz (the band-edge and tuning grid).

## Memory layout

| What | Size | Notes |
|---|---|---|
| Codeplug (all of it) | `0x280` bytes at `0xD80`, the CPU EEPROM | Read 20 × 32 bytes; writable part `0x0E13`–`0x0FFB` |
| `.per` file | `0x7F1` bytes (new format) / `0x799` bytes (old format) | A DPS file format, built from the 640 bytes; not a radio region |

The `.net`, `.per` and `.rad` file formats (a repeating 7-byte XOR key
`5D 29 EA 64 B3 7F 1C`) and the field offsets are documented separately in the Motorola RE
project (`docs/DPS_EMEA_600SERIES_R00.03.02/FILE_FORMAT.md` and `FIELD_MAP.md`).

## Confirmed on a GM600

First session, Glasgow revC3 on J0400-6/7, 9 V on HOOK (2026-11-03):

```
> F1 10 FE                       enter programming mode
< 50                             ACK, 1.3 ms after the echo; then the radio resets
  (radio reboots: 23/13 sent right after, even 21 ms after the ACK, get no reply)

  after the reboot, without sending 10 again:
> F2 23 01 E9                    read ID string 1
< 50  FC 8B 01 38 30 37 49 59 41 30 39 30 33 29
         8B  01  "807IYA0903"  cks         -> index 1 is the serial number
> F1 13 FB                       max block size
< 50  F4 82 00 20 00 69
```

- ACK is `50`, not `05`. Both reply checksums are valid.
- `10` makes the radio reset. After the reset it answers commands with no further `10`, so
  the reset looks like the way into programming mode. Wait for the radio to come back up
  before the next command. How long that takes hasn't been measured, and nobody knows how DPS
  handles it with its 21 ms wait.
- Without 9 V on HOOK, `F1 10 FE` gets only its echo, and no ACK.
- `8B` replies repeat the index before the string and have no NUL terminator.
- `82` carries 3 data bytes (`00 20 00`), not 2. What they mean is open: 64-byte reads work,
  so it isn't a 32-byte limit.
- `23` indexes: 0 → `02 09` (binary, maybe a model code), 1 → `807IYA0903` (serial), 3 →
  `R03.03.21` (firmware version), 4 → `02.03` (probably the codeplug version). 3 and 4 end
  in a NUL; 1 doesn't.
- `11` reads the **CPU's 64 KB logical address space**: the top address byte is ignored and
  reads wrap every 64 KB. The `80` reply header is the 3-byte address as sent.
- Reads of up to 64 bytes work. A 128-byte read is refused with a bare `80` in place of the ACK.
- Memory map seen through `11` (summary; [FIRMWARE.md](FIRMWARE.md) has the full map and how it
  was derived):

  | Range | Contents |
  |---|---|
  | `0000`–`02FF` | internal RAM (changes between reads) |
  | `0D80`–`0FFF` | CPU internal EEPROM, 640 bytes = the "alignment block" |
  | `1000`–`107F` | CPU register block (bank select `0x1058`, SCI data `0x1077`); live, changes between reads |
  | `4000`–`7FFF` | banked 16 KB flash window: shows whichever bank is mapped in at the time |
  | `8000`–`DAFF` | fixed firmware (`Copyright 1992, Motorola, Inc.`, `R03.03.21`), vectors at `FFD6` |

- The alignment block starts `02 50 7F EA 4E B4 02 09 "807IYA0903CG" …`. Bytes 6–7 are the
  `02 09` that `23 00` returns, so the ID queries read from this EEPROM. Its 640 bytes do **not**
  sum to `0xFF` (they sum to `0xB0`), so DPS's region checksum covers a sub-range or uses
  another scheme.
- The board only has a 256 KB flash (U0102), a 32 KB "hybrid" SRAM (U0103, probably
  battery-backed) and the CPU EEPROM. The codeplug is the CPU EEPROM (see "Reading and writing a
  radio"); I first assumed a separate 2 KB personality region behind paging, which was wrong.

## Other traffic on BUS+

On the GM600, BUS+ carries nothing in normal use: the control head talks to the CPU over SPI
and analog key lines, not BUS+ (controller schematic, J0101). This was confirmed on a radio: no
BUS+ activity while pressing buttons. Keep the parser robust anyway:

- Resync on frame boundaries using the checksum, and ignore frames that aren't the reply you
  expect.
- The applet only starts sending once the line has been idle for 10 bit times, and drops a
  frame if it detects a collision. Retry when the echo doesn't match.

## Open questions

- Which `23` index returns the model and which the radio info? (Index 1 is the serial.)
- How long the reset after `10` takes, and whether the radio needs anything before it accepts
  commands again.
- What the `82` reply bytes mean.
- What `21` does. DPS doesn't send it to a GM600.
- What exactly do `20`, `22` and `19` do?
- Does the radio enforce its own limit on the gap between received bytes? DPS allows 121 ms on
  its side.
- Where pins 6, 7 and 14 physically sit on the J0400 face. The wiring works, but the
  layout isn't recorded yet.

DPS runs under QEMU with its COM port bridged to the applet's `socket` (via `socat`), so more
of these can be settled by capturing DPS sessions. The `20`/`22` tuning commands are the
interesting remaining ones.
