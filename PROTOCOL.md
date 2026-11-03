# GM600 / GP600 Programming Protocol (SBEP variant)

Host-side protocol for programming 600 Series radios (GM600 / GP600, MPT1327 trunked) over
the BUS+ line, through the `glasgow_rib` applet (`socket` or `pty` mode).

Everything here was reverse engineered from Motorola's DPS (`WDPS_DP.EXE`, R00.03.02, a 16-bit
Windows 3.1 program). It has **not yet been checked against a real radio**. The open questions
are listed at the end.

## Physical layer

- 9600 baud, 8N1, no flow control. DPS never uses any other baud rate.
- One wire, BUS+, shared by both sides (half duplex, open drain). The GM600 has no BUSY line.
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
| `10` | Enter programming mode | none | ACK |
| `13` | Ask the largest block size the radio accepts | none | ACK, then `82` frame: big-endian 16-bit size |
| `11` | Read a block | `len a23-16 a15-8 a7-0` (24-bit address) | ACK, then `80` frame: 3 header bytes + `len` data bytes |
| `17` | Write a block | `a23-16 a15-8 a7-0 data…` | ACK, then `84` (OK) or `85` (failed) |
| `23` | Read an ID string | `[index]`: 0, 1, 3 and 4 are used; 1 is read at connect | ACK, then `8B` frame: NUL-terminated string of up to 13 characters |
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

## Reading a radio (what DPS does)

1. `10`: enter programming mode, up to 3 tries.
2. `23 01`: read the ID string at index 1 (probably the serial number).
3. `13`: ask the largest block size the radio accepts.
4. For each memory region: optionally `21` to preset the address, then a loop of `11` reads.
   - Chunk size = `clamp(min(total / 20, radio_max), 0x20, 0x100)`.
   - The address goes up by one chunk on each read.
5. Read the **0x280-byte alignment block at radio address `0xD80`**.
6. Check each region's checksum (`0xFF − sum`, the same scheme as the files).

DPS has no "leave programming mode" command. The radio drops out on a timeout or a power cycle.

Writing works the same way, with `17` blocks, each answered by an `84`/`85` result frame.
Before writing, DPS checks that the radio's serial number matches the one in the loaded data,
so a codeplug read from one radio can't be written to another.

## Memory layout

| What | Size | Notes |
|---|---|---|
| Personality region | `0x7F1` bytes (new format) / `0x799` bytes (old format) | Same contents as the `.per` file body |
| Alignment block | `0x280` bytes at `0xD80` | Also stored at offset `0xD80` in archive files |

The `.net`, `.per` and `.rad` file formats (a repeating 7-byte XOR key
`5D 29 EA 64 B3 7F 1C`) and the field offsets are documented separately in the Motorola RE
project (`docs/DPS_EMEA_600SERIES_R00.03.02/FILE_FORMAT.md` and `FIELD_MAP.md`).

## Watch out for other traffic on BUS+

The GM-series service manual (GM340 family, in `docs/gm-manual.txt`) shows the radio talking to
its control head over the same BUS+ line. Expect the same on the GM600:

- The radio sends LED and backlight state to the control head, and asks it for key presses.
- The control head ignores CPS/tuner traffic.

So your parser must not assume every received byte is a reply to you:

- Resync on frame boundaries using the checksum, and ignore frames that aren't the reply you
  expect.
- The applet only starts sending once the line has been idle for 10 bit times, and drops a
  frame if it detects a collision. Retry when the echo doesn't match.

I couldn't confirm this for the GM600 specifically: the GM600 manual in `docs/` only contains
board layouts and parts lists.

## Open questions

- Is the 3-byte header in an `80` reply `addr_hi addr_mid addr_lo` or `len addr_hi addr_mid`?
- Which `23` index returns the model, which the serial number, and which the radio info?
- What exactly do `20`, `22` and `19` do?
- Does the radio enforce its own limit on the gap between received bytes? DPS allows 121 ms on
  its side.
- Which pin carries BUS+ on the accessory connector (J0400) and the control head connector
  (J0101)? It needs confirming on hardware.

The quickest way to settle all of these: run the real `WDPS_DP.EXE` in Windows 3.11 under
86Box, point its COM1 at the applet's `pty`, and capture one full read and write session (or
watch with `sniff`). Then make your CPS reproduce that session byte for byte.
