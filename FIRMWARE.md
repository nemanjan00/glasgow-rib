# GM600 firmware notes

Static analysis of a GM600 firmware image read out over BUS+ with the `11` command. This is
background for the protocol: it explains *why* the radio behaves the way [PROTOCOL.md](PROTOCOL.md)
records, and it settles some of that file's open questions. Nothing here needs a radio to verify;
it all comes from the dump.

## The dumps (`docs/`, not in git)

| File | What |
|---|---|
| `gm600-807IYA0903-2026-11-03-cpu64k.bin` | The full 64 KB CPU logical address space, read with `11`. |
| `gm600-807IYA0903-2026-11-03.bin` | The first 4 KB (low RAM + internal EEPROM). Matches the 64 KB dump except for ~19 bytes of live RAM (`0x000C`–`0x028A`) that changed between reads. |

`807IYA0903` is the radio's serial number (from `23 01`). Firmware version `R03.03.21`, copyright
string "Copyright 1992, Motorola, Inc." at `0x803C`.

These are a snapshot of one radio's RAM/ROM as the CPU sees it, **not** a dump of the flash chip.
The flash is banked (below), so this view shows only the one bank that happened to be mapped in,
plus RAM, registers and the internal EEPROM.

## CPU and memory

The controller CPU is a **Motorola 68HC11 K-series** part. The two sources in the manual disagree
on the exact variant: the parts list text calls U0101 a masked **68HC11K4** (`5195136A01`, "S/W
MSKD 68HC11K4"), while the schematic symbol is labelled **68HC11K1_QFP**. Either way it has the
K-series memory-management unit (bank register at `0x1058`, MMU control registers around offset
`0x55`–`0x5F`) and 640 bytes of internal EEPROM. The 64 KB logical map seen through `11`:

| Range | Contents |
|---|---|
| `0000`–`02FF` | Internal RAM (768 bytes) |
| `0D80`–`0FFF` | **Internal EEPROM, 640 bytes** — the "alignment block": serial `807IYA0903CG`, tuning |
| `1000`–`107F` | **I/O register block** — bank select `0x1058`, SCI data `0x1077`, free-running timer `0x100E`. Values here change between reads because they are live registers, not RAM. |
| `1080`–`3FFF` | RAM / working data structures (external SRAM U0103, inferred) |
| `4000`–`7FFF` | **Banked flash window** (see below) |
| `8000`–`DAFF` | Fixed firmware bank (dispatcher, SCI driver, trampoline, vectors) |
| `DB00`–`FFBF` | `FF` (unmapped in this bank) |
| `FFC0`–`FFFF` | Interrupt vectors |

The register block sits at `0x1000`, not `0x0000` (the 68HC11 register block is relocatable; this
one is placed at `0x1000`). That is why `0x1058` and `0x1077` are registers and why the `0x1000`
page changed between reads.

### Flash banking

The radio has a 256 KB flash (U0102, 28F020) behind a 64 KB CPU. It is paged into the
`4000`–`7FFF` window:

- **Bank select register = `0x1058`.** Writing it changes which 16 KB flash page appears at
  `4000`.
- Banks used by the firmware are **`00 04 08 0C 10 14 1C 20 24 28 2C`** — eleven 16 KB pages.
  Note `0x18` is skipped. `0x1058 = 0` also selects the register/RAM page for some accesses; the
  reset init writes it last.
- Far calls into a banked routine go through a **trampoline at `0x87B5`**: it saves the current
  `0x1058`, sets bank `2`, reads a 3-byte `[bank, addr_hi, addr_lo]` descriptor, switches to the
  target bank, `jsr`s, then restores the old bank. A table of 504 fixed call stubs starts at
  `0x87D9` (each `jsr $87B5`). The trampoline adds `0x4CAC` to its **return address** (the stub +
  3), so each descriptor is at `stub + 3 + 0x4CAC`. (At `stub + 0x4CAC` the first descriptor would
  decode as bank `0x7E`, which is impossible; with `+3` every bank is a multiple of 4.)

Consequence for the protocol: **the 2 KB personality region is not visible in the 64 KB `11`
view.** It lives in flash (or battery-backed SRAM, U0103) reachable only once the right page is
selected, which is almost certainly what `21` does before a codeplug read. Don't guess `21`
values — capture them from DPS.

## SCI (the BUS+ UART)

- SCI data register `0x1077`, status/control around `0x1074`. The byte-level driver is in bank
  `04` (`~0x44DF`–`0x49A4`).
- Reset initialises the SCI and related registers from a table at `0xD437` (`reg, value` pairs,
  terminated by the first value ≥ `0x80`): baud/format regs plus the K-series
  expanded MMU/SCI set.

## SBEP command dispatch — the authoritative command list

The command handler is a **jump table at `0x4019` in bank `04`**, indexed by the SBEP opcode
(entry = `0x4019 + 2*opcode`, big-endian, bank-04 address). Any opcode whose entry is `0x8236`
falls through to a shared "unsupported" stub. The populated entries:

| Opcode | Handler | Meaning |
|---|---|---|
| `10` | `4CD5`→`9BCA` | Enter programming mode (deliberate hang → watchdog reset) |
| `11` | `4D85` | Read block |
| `13` | `4C8E` | Query max block size |
| `17` | `4CE8` | Write block |
| `18` | `4E49` | **Self-test / verify** (read-only) — not in PROTOCOL.md |
| `19` | `4F30` | Query (shared message-post path) |
| `20` | `4F3F` | Parameter read / softpot write |
| `21` | `4F30` | Query (shared message-post path) |
| `22` | `4F30` | Query (shared message-post path) |
| `23` | `4F30` | Read ID string (shared message-post path) |
| `26` | `4CDC` | **Jump to an address taken from the request — factory backdoor** — not in PROTOCOL.md |
| `2D` | `8B8A` | unbanked handler |
| `2E` | `8B87` | unbanked handler |

Any opcode whose table entry is `0x8236` (e.g. `12`, `14`–`16`, `1A`–`1F`, `24`, `25`, `27`+)
falls through to a shared "unsupported" stub and gets no useful reply.

### Handler details

- **`10` enter programming (`4CD5` → `9BCA`).** The routine is just `sei; bra self` — a tight
  infinite loop with interrupts off. The COP watchdog then times out and resets the CPU, which
  comes back up in programming mode. This is exactly the "ACK, then the radio resets" behaviour in
  PROTOCOL.md: the reset is intentional, not a crash.

- **`11` read (`4D85`).** Reply opcode `0x80`. Copies the request's 3 address bytes straight into
  the reply header, then appends the data (see open-question answers below).

- **`13` max block size (`4C8E`).** Returns a **hardcoded constant `0x20`**, formatted as a 3-byte
  `82` reply — this is the observed `00 20 00`. It is not computed from anything (RAM, model,
  etc.), so treat the `82` payload as a fixed advertised value, not a hard limit; 64-byte reads
  work in practice.

- **`17` write (`4CE8`).** Reply `0x84` (ok) / `0x85` (fail). Same `0D80`–`0FFF` → internal-EEPROM
  special-case as the read path.

- **`18` self-test (`4E49`).** Sub-command in the first data byte: `0` reads and status-checks a
  small fixed region; `1` runs a checksum/compare over a region. Returns a status byte. Read-only,
  but non-essential — skip it unless you want the self-test.

- **`19`/`21`/`22`/`23` query (`4F30`).** All four post a message (via `8377`, the inter-task
  message primitive that also drives the SCI) to a task that formats the reply. `23`'s string
  reply (`8B`) is built in that task, which lives in a **flash bank not present in this dump** —
  that's why no `ldab #$8B` appears in bank 04. The sub-selection between these four opcodes
  happens in the task, not in this handler.

- **`20` parameter (`4F3F`).** If the second data byte is `0` it is a **read**: reply `8A` echoing
  the parameter index plus its value. If it is `1` or `2` it is a **write** (two flavours, via
  `8B72`), matching the RAM-vs-permanent guess in PROTOCOL.md.

- **`26` backdoor (`4CDC`).** `sei`, load a 16-bit pointer from the request data, `jmp` through it.
  This executes arbitrary code at an address the host supplies. **Never send `26` blind** — a
  wrong pointer runs garbage with interrupts disabled. It is, however, a clean way to dump the
  other flash banks if you ever want to (point it at a small stub, or use it to drive the bank
  register), but only with a known-good routine.

Findings worth carrying into PROTOCOL.md:

1. **Opcodes `18` (self-test) and `26` (arbitrary jump) exist** beyond what DPS sends. `18` is
   safe to probe read-only; `26` is a backdoor — document it, don't fire it.
2. **`19`, `21`, `22`, `23` share the query entry point `4F30`** (proven from the dispatch table
   and the handler, which posts a message rather than taking the write path). This suggests
   `21`/`22`/`19` are query sub-variants rather than destructive writes — but the reply is
   formatted by a task in a flash bank not in this dump, so their *final* behaviour is not proven
   here. Treat "21 is not a destructive write" as likely, not confirmed, until a DPS capture or a
   dump of the other banks settles it.
3. **`13`'s advertised size is a constant**, so don't treat `00 20 00` as the real transfer
   ceiling.

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

## Is the codeplug in the dump? No.

The stored personality codeplug (the `0x7F1`-byte `.per` body) is **not** present in this 64 KB
view. What is here:

- **Internal EEPROM, `0D80`–`0FFF` (640 bytes).** Holds the identity string `807IYA0903CG` (serial
  + model suffix) and binary tuning/softpot data. This is the **alignment block**, not channels.
- **RAM, `1080`–`3FFF`.** Pointer tables and working structures. A blank DPS `.rad`/`.net` body
  cross-matches here only in short (≤13-byte) runs around `3300`–`3C00`, which is low-signal (the
  DPS sample is a default/empty codeplug). At most this is an unpacked working copy, not the
  stored codeplug.

The channel/personality data therefore lives somewhere not in this logical snapshot — the
**battery-backed SRAM (U0103, 32 KB)**, which isn't in the 64 KB window, or an **unmapped flash
bank**. That matches the protocol: DPS presets an address/page with `21` before reading the
codeplug region, i.e. the codeplug is paged in, not sitting in the default map.

To capture a real codeplug: read it over BUS+ with `11` after the correct `21` page select (get
the `21` values from a DPS session), or read U0103 directly. The `0D80`–`0FFF` alignment block is
already readable with plain `11` reads.

## Limitation: only one flash bank is in this dump

The `11` dump is the CPU's 64 KB *logical* view, so it contains **only the flash bank that was
mapped at `4000`–`7FFF` when it was read** (bank `04`), plus the fixed `8000`+ region, RAM and the
EEPROM. The other eleven banks — including the query-reply formatter and, almost certainly, the
personality codec — are not here.

To get the rest you either:

- **Read the flash chip** (U0102, 28F020) with a programmer — the complete, reliable route; or
- **Dump bank by bank over BUS+**: set `0x1058` to each of `00 04 08 0C 10 14 1C 20 24 28 2C` (0x18 is unused) and read `4000`–`7FFF`.
  Changing `0x1058` needs either a register write (`17` to `0x1058`, risky) or whatever `21`
  selects. Work the `21` values out from a DPS capture before trying this.

## How these addresses were found (so they can be re-derived)

- Disassembled with capstone (`CS_ARCH_M680X` / `CS_MODE_M680X_6811`) against the 64 KB dump.
- Dispatch table located by searching bank `04` for the `[00 01 …]` header preceding a run of
  `82 36` (the unsupported-stub address) with real handler addresses at the known opcode indices.
- Bank register `0x1058` found from the reset init and the trampoline at `0x87B5`.
- Reply opcodes confirmed by the immediate loads in each handler (`ldab #$80` at the `11` handler,
  etc.).
