# GM600 firmware notes

Static analysis of a GM600 firmware image read out over BUS+ with the `11` command. This is
background for the protocol: it explains *why* the radio behaves the way [PROTOCOL.md](PROTOCOL.md)
records, and it settles some of that file's open questions. Nothing here needs a radio to verify;
it all comes from the dump.

## The dumps (`docs/`, not in git)

| File | What |
|---|---|
| `gm600-807IYA0903-2026-11-03-cpu64k.bin` | The full 64 KB CPU logical address space, read with `11`. |
| `gm600-807IYA0903-2026-11-03.bin` | The first 4 KB (registers + RAM). Matches the 64 KB dump except for ~19 bytes of live RAM that changed between reads. |

`807IYA0903` is the radio's serial number (from `23 01`). Firmware version `R03.03.21`, copyright
string "Copyright 1992, Motorola, Inc." at `0x803C`.

These are a snapshot of one radio's RAM/ROM as the CPU sees it, **not** a dump of the flash chip.
The flash is banked (below), so this view shows only the one bank that happened to be mapped in,
plus RAM, registers and the internal EEPROM.

## CPU and memory

The controller CPU is a **Motorola 68HC11K1** (the schematic calls U0101 a 68HC11K4; the K1 is
the mask variant actually fitted). The 64 KB logical map seen through `11`:

| Range | Contents |
|---|---|
| `0000`–`03FF` | I/O registers and internal RAM. Changes between reads. |
| `0400`–`0CFF` | RAM (mostly zero on an idle radio) |
| `0D80`–`0FFF` | **Internal EEPROM, 640 bytes** — the "alignment block": serial `807IYA0903CG`, tuning |
| `1000`–`3FFF` | RAM / working data structures |
| `4000`–`7FFF` | **Banked flash window** (see below) |
| `8000`–`DAFF` | Fixed firmware bank (dispatcher, SCI driver, trampoline, vectors) |
| `DB00`–`FFBF` | `FF` (unmapped in this bank) |
| `FFC0`–`FFFF` | Interrupt vectors |

### Flash banking

The radio has a 256 KB flash (U0102, 28F020) behind a 64 KB CPU. It is paged into the
`4000`–`7FFF` window:

- **Bank select register = `0x1058`.** Writing it changes which 16 KB flash page appears at
  `4000`.
- Banks used by the firmware are `0x00`, `0x04`, `0x08` … `0x2C` (steps of 4) — twelve 16 KB
  pages. `0x1058 = 0` also selects the register/RAM page for some accesses; the reset init writes
  it last.
- Far calls into a banked routine go through a **trampoline at `0x87B5`**: it saves the current
  `0x1058`, sets bank `2`, reads a 3-byte `[bank, addr_hi, addr_lo]` descriptor, switches to the
  target bank, `jsr`s, then restores the old bank. A table of 504 fixed call stubs starts at
  `0x87D9` (each `jsr $87B5`), with the matching `[bank, addr]` descriptors at `stub + 0x4CAC`.

Consequence for the protocol: **the 2 KB personality region is not visible in the 64 KB `11`
view.** It lives in flash (or battery-backed SRAM, U0103) reachable only once the right page is
selected, which is almost certainly what `21` does before a codeplug read. Don't guess `21`
values — capture them from DPS.

## SCI (the BUS+ UART)

- SCI data register `0x1077`, status/control around `0x1074`. The byte-level driver is in bank
  `04` (`~0x44DF`–`0x49A4`).
- Reset initialises the SCI and related registers from a table at `0xD437` (`reg, value` pairs,
  terminated by the first value ≥ `0x80`): baud/format regs `0x2B`/`0x2C`-era plus the K1's
  expanded set.

## SBEP command dispatch — the authoritative command list

The command handler is a **jump table at `0x4019` in bank `04`**, indexed by the SBEP opcode
(entry = `0x4019 + 2*opcode`, big-endian, bank-04 address). Any opcode whose entry is `0x8236`
falls through to a shared "unsupported" stub. The populated entries:

| Opcode | Handler | Meaning |
|---|---|---|
| `10` | `4CD5` | Enter programming mode / reset |
| `11` | `4D85` | Read block |
| `13` | `4C8E` | Query max block size |
| `17` | `4CE8` | Write block |
| `18` | `4E49` | **Implemented, not yet in PROTOCOL.md** — purpose unknown; probe read-only |
| `19` | `4F30` | (shared query handler) |
| `20` | `4F3F` | Parameter / softpot |
| `21` | `4F30` | (shared query handler) |
| `22` | `4F30` | (shared query handler) |
| `23` | `4F30` | Read ID string (shared query handler) |
| `26` | `4CDC` | **Implemented, not yet in PROTOCOL.md** — purpose unknown; probe read-only |
| `2D` | `8B8A` | unbanked handler |
| `2E` | `8B87` | unbanked handler |

Two findings worth carrying into PROTOCOL.md:

1. **Opcodes `18` and `26` exist** in addition to the ones DPS sends. They are candidates for
   read-only probing (never blind-write an unknown opcode).
2. **`19`, `21`, `22` and `23` all enter the same handler `4F30`.** `23` (read ID string) is the
   general "query" path; `21`/`22`/`19` are sub-variants dispatched inside it, which fits the idea
   that `21` is a query/preset rather than a destructive write on its own.

## Answers to PROTOCOL.md open questions

From the `11` read handler at `4D85` and the `17` write handler at `4CE8`:

- **The `80` read-reply 3-byte header is the address, echoed as sent** (`addr_hi, addr_mid,
  addr_lo`). The handler copies request bytes 20/21/22 (the 3 address bytes) straight into the
  reply header, then appends the data. It is *not* `[len, addr_hi, addr_mid]`.
- **Only the low 16 bits of the address are used.** `addr_hi` is copied into the reply header but
  ignored for addressing, which is why reads wrap every 64 KB.
- **`0D80`–`0FFF` is special-cased.** Both the read and write handlers do a range check
  (`addr − 0x0D80`, then `addr − 0x1000`); addresses in that window are served from / written to
  the CPU's internal EEPROM, everything else from the normal memory read/write path. This is the
  "alignment block" and it is why that region is the radio's non-volatile tuning store.

Still open (need a real radio or a DPS capture, as PROTOCOL.md says): the meaning of the `82`
size bytes, exactly what `21` pages in, and the behaviour of `18`, `20`, `22`, `26`.

## How these addresses were found (so they can be re-derived)

- Disassembled with capstone (`CS_ARCH_M680X` / `CS_MODE_M680X_6811`) against the 64 KB dump.
- Dispatch table located by searching bank `04` for the `[00 01 …]` header preceding a run of
  `82 36` (the unsupported-stub address) with real handler addresses at the known opcode indices.
- Bank register `0x1058` found from the reset init and the trampoline at `0x87B5`.
- Reply opcodes confirmed by the immediate loads in each handler (`ldab #$80` at the `11` handler,
  etc.).
