# DPS data files (`.net` / `.per` / `.rad`)

The vendor Dealer Programming Software (DPS, Win3.1, R00.03.02–R00.05.04) and its Network Programmer
save radio data in their own file formats. None of these exist in the radio: the radio's whole
codeplug is the 640-byte EEPROM ([CODEPLUG.md](CODEPLUG.md)), and DPS builds these files from it. This
page matters if you want to read or produce DPS files, or reuse their field semantics.

Ported from the Motorola CPS RE project (`docs/DPS_EMEA_600SERIES_R00.03.02/`). **That source was
static analysis and was not proven.** Everything below was re-checked on 2026-11-12 against five files.
Two are the samples bundled with DPS (`DPS.NET`, `DPS.RAD`). Three were saved by DPS from the bench
radio (`radio.per`, `radio_mod.per`, `rad1.rad`). The radio's EEPROM read (`native-eeprom.bin`) was
the comparison point.

Evidence tags:

- **[HW]** checked against data read from the radio.
- **[DPS]** checked against files the vendor tool wrote: samples, or a GUI edit followed by a diff.
- **[INF]** static analysis of the DPS executables only. Not proven.

## Container

| Offset | Size | Content | Evidence |
|--|--|--|--|
| `0x00` | 9 | magic `9B 37 F2 B1 C9 5E 9A 40 1D` | [DPS] 5/5 files |
| `0x09` | 2 | LE type: 0 = `.net`, 1 = `.per`, 2 = `.rad` | [DPS] all three types seen |
| `0x0C`–`0xD2` | | reserved; untouched files hold the buffer-init filler `F6 39 2B A8 0D 51 C7 4D C2` (not crypto) | [INF] |
| `0xD3` | 2 | LE format: `0x0300` = NEW (DPS), `0x0200` = OLD (GP1200/GM1200 RSS GVN6007) | [DPS] `0x0300` in 5/5 files; OLD never seen |
| `0xDB` | 1 | header checksum = `(Σ bytes 0x00..0xDA − 0x55) & 0xFF` | [DPS] 5/5 |
| `0xDC` … EOF−2 | | body: mostly little-endian 32-bit fields | [DPS] |
| EOF−1 | 1 | trailing checksum = `Σ all preceding bytes & 0xFF` | [DPS] 5/5 |

The header checksum is the same `(Σ − 0x55)` rule the radio uses for its EEPROM blocks
([CODEPLUG.md](CODEPLUG.md)).

**Obfuscation:** `.net` and `.rad` are XORed with a repeating 7-byte key `5D 29 EA 64 B3 7F 1C` (phase
0 at offset 0). [DPS] The `.per` files DPS R00.05.04 saved from the radio are **plaintext**. [DPS] The
source claimed `.per` is XORed too; that was never seen.

| File | NEW size | OLD size |
|--|--|--|
| `.net` | 1213 B [DPS] | 1189 B [INF] |
| `.per` | 2033 B (`0x7F1`) [DPS] | 1945 B (`0x799`) [INF] |
| `.rad` | 3025 B [DPS] | 2913 B [INF] |

```python
KEY7  = bytes.fromhex('5d29ea64b37f1c')
MAGIC = bytes.fromhex('9b37f2b1c95e9a401d')

def load(path):
    raw = open(path, 'rb').read()
    d = bytearray(raw if raw[:9] == MAGIC else bytes(b ^ KEY7[i % 7] for i, b in enumerate(raw)))
    assert d[:9] == MAGIC
    assert (sum(d[:0xDB]) - 0x55) & 0xFF == d[0xDB]
    assert sum(d[:-1]) & 0xFF == d[-1]
    return d   # type = d[9], format = int.from_bytes(d[0xD3:0xD5], 'little')

def store(path, d, xor=True):
    d[0xDB] = (sum(d[:0xDB]) - 0x55) & 0xFF
    d[-1] = sum(d[:-1]) & 0xFF
    open(path, 'wb').write(bytes(b ^ KEY7[i % 7] for i, b in enumerate(d)) if xor else bytes(d))
```

## `.rad` = network block + personality

- `0x000`–`0x4BC`: a network block with the same layout as a `.net` file. [INF]
- `0x4BD`: personality section tag `02 00`. [DPS]
- `0x4C0`: radio serial, 10 ASCII characters + NUL. It is the radio's serial in `rad1.rad`. [HW]
- In a `.per` the serial is at `0x0E0` and the conventional table at `0x4F7`, so the personality
  sits about `0x3E0` lower than in a `.rad`. [HW] The exact `.per` ↔ `.rad` mapping is not worked out.

### Conventional personality — `.rad` `0x8D7`, 4 × 32 bytes, eight LE dwords per channel

| +off | Field | Encoding | Evidence |
|--|--|--|--|
| `+0x00` | Rx frequency | Hz | [HW] all 4 channels match the EEPROM |
| `+0x04` | Tx frequency | Hz; 0 = no more entries | [HW] incl. ch 4's split 438.625 / 431.025 MHz |
| `+0x08` | Tx Power | 0 Low, 1 High | [HW] 1 on all channels; EEPROM option bit 3 set |
| `+0x0C` | Tx Inhibit | 0 Disabled, 1 On Busy, 2 On Busy Unless PL | [INF] (0 in every file) |
| `+0x10` | Rx PL | tone index, 0 = none | [INF] (0 in every file) |
| `+0x14` | Tx PL | tone index, 0 = none | [HW] ch 4 = 19, the same value as EEPROM `+4` |
| `+0x18` | Monitor | 0 / 1 | [INF] |
| `+0x1C` | Data channel | 0 / 1 | [INF] |

The tone list (index → Hz) is the `pl_tone` enum in [gm600_codeplug.ksy](gm600_codeplug.ksy).
Index 19 = 123.0 Hz is [HW]: the HackRF squelch test opened PL on that index. The rest of the list is
the standard Motorola order [INF]. The DPS help text has typos in some 2-letter codes, so trust the
Hz values.

Other personality regions [INF, low confidence]:

- `0x4CB`–`~0x650`: alignment / band-calibration tables. Reference 375.000 MHz at `0x623`, band
  edges at `0x633`–`0x64B`.
- `0x7F7`: 14 × 16-byte records, possibly shortform dial.
- `0x957`: unit ID / dial string.
- `~0x9D6`+: call-list ASCII in filler-padded slots.

## `.net` body (`0xDC`–`0x4BB`)

Dword *n* is at `0xDC + 4n`. The group boundaries come from the loop counts in the "create default
`.net`" function. [INF]

| Offsets | n | Group |
|--|--|--|
| `0x0DC`–`0x1E3` | 0–65 | 66 single fields: network, hunt, radio flags, counters NC1..NW, timers TA..TW (order mostly unmapped) |
| `0x1E4`–`0x22B` | 66–83 | Preferential NDD: 6 × (priority, field length, sub-field) |
| `0x22C`–`0x3AB` | 84–179 | Control channel list: 32 × (channel 1..1023, time-shared, home) |
| `0x3AC`–`0x3EB` | 180–195 | Access authorization: 8 × (ident, class 0 Area / 1 Full / 2 Zone) |
| `0x3EC`–`0x4AB` | 196–243 | Channel frequency plan: 16 × (channel, Rx Hz, Tx Hz); entry 0 = trunked base |
| `0x4A0`–`0x4BB` | | tail, 7 dwords: Radio Config 1, Radio Config 2, NVote, lowest / highest system channel, LMVote, TVote |

- The frequency plan has **15** entries, not 16, so the tail starts at `0x4A0`. [HW]
  - `rad1.rad` has highest system channel = 80 and plan entries for channels 0, 1 and 80.
  - The rest of that plan is unused slots full of filler, not zeros. Stop at the first all-garbage
    entry, not at a zero.
  - Entry 0 for the bench radio is 440.99375 / 445.99375 MHz.
- The control channel list in `rad1.rad` (12, 9, 5, 11, 3, 7, 2, 10) is the same list, in the same
  order, as 2-byte entries at EEPROM `0x0E5D`–`0x0E6C` (channel in the low byte). [HW]
- The other boundaries are [INF].
- Frequencies are multiples of 1250 Hz (manual). [INF]

**The 66 single fields are the EEPROM network block, packed.** [HW, one radio] Comparing `rad1.rad`
with the same radio's EEPROM, the dword values appear in order from `0x0E13`, packed by size. 35
consecutive values match.

| EEPROM | Single field(s) | Packing |
|--|--|--|
| `0x0E13` | #0–#1 | bits, unresolved (`0x80` for 1, 0) |
| `0x0E14` | #2, #3 | nibbles (`0x45` = 4, 5) |
| `0x0E15`–`0x0E25` | #4–#20 | one byte each |
| `0x0E26`–`0x0E27` | #21 | BE16 (515) |
| `0x0E28`–`0x0E2E` | #22–#28 | one byte each |
| `0x0E2F` | #29, #30 | nibbles (`0xAA` = 10, 10) |
| `0x0E30`–`0x0E32` | #31–#33 | one byte each |
| `0x0E33`–`0x0E36` | #34–#37 | unclear; `0x0E35` = #36, `0x0E36` = #37 |
| `0x0E37`– | #38+ | nibble pair `0x11`, then BE16 words; the order is not one-to-one |

Which names these singles carry (counters NC1.., timers TA.., hunt options) is still unmapped. One
GUI edit + DPS write + EEPROM diff per field would bind them.

**Radio Flags dwords.** A Network Programmer GUI-edit contrast mapped three fields. [DPS]

| Field | Dword | Offset | Values |
|--|--|--|--|
| Registration on power-up | #46 | `0x194` | 0 = off, 1 = on |
| De-/pre-emphasis | #48 | `0x19C` | 0 = MPT1327, 1 = PAA1382 |
| Conventional Mode | #51 | `0x1A8` | 0 = Inhibit, 1 = Dealer, 2 = All, 3 = RegioNet43 |

`rad1.rad` from the bench radio has Conventional Mode = 2. [HW: the radio does allow conventional]

**Contradicted:** the source's decode of a "packed Radio Config Byte1" at `0x4A0` does not hold.

| | Claimed packing | File value |
|--|--|--|
| `DPS.NET` | Conventional Mode = 2 should give bits 6:5 = `10` | `0xE7` has bits 6:5 = `11` |
| `rad1.rad` | Registration = 0 should give bit 7 = 1 (inverted) | `0x1A` has bit 7 = 0 |

Treat `0x4A0` as unidentified.

## Vendor tool facts

- Two installs:
  - `DPSWNP` (radios without a data panel): `WDPS_NP.EXE` Network Programmer plus the Dealer
    Programmer resources.
  - `DPSWDP` (radios with a data panel): `WDPS_DP.EXE`.
- 16-bit Windows 3.1 NE executables, Zinc Application Framework 4.0. `WSERIAL.EXE` is a dealer
  serializer that patches identity and service password into the EXE, not a comms tool. [INF]
- What actually ran DPS against this radio: Windows 3.1 in QEMU on the lab host, with the COM port
  bridged to the rib applet. [HW] See [DPS_GUI_MAP.md](DPS_GUI_MAP.md) for the menus.
- 28 model numbers are embedded in both EXEs, pattern `M08{band E/K/N/P/R}H{tier A/E/F}{ch 4/6/8}CK{rev
  1-3}`, plus `BN` variants. [INF] No GP600 numbers were found in these binaries.
- Network data the Network Programmer edits (from the bundled manual):
  - MPT network specific: network type, dialling plan RegioNet43 / MPT1343 / Dutch PTT, home zone,
    control category, operator ID;
  - hunt options and RSSI margins;
  - radio flags;
  - preferential NDD;
  - access authorization;
  - control channel list;
  - frequency plan;
  - counters and timers.
- The motorola-cps project also extracted the DPS field help (158 fields) and the 195-entry field
  registry from the vendor resources. They are not reproduced here because they are vendor text. The
  registry holds UI metadata (labels, layout), not buffer or EEPROM offsets. [INF]

## Source claims that were wrong

These come from the source `SETUP_GUIDE.md`.

| Source claim | Why it's wrong |
|--|--|
| A diode-only RIB-less cable on the mic connector is enough | Never tested. The working link is BUS+ on J0400-6 with **~9 V on HOOK**; the radio hears but doesn't answer without it. See [CONNECTION.md](CONNECTION.md). |
| "Read pulls `0x7F1` personality + a `0x280` alignment block @ `0xD80`" | The radio has only the 640-byte EEPROM at `0xD80`, and it is the whole codeplug, not an alignment block ([DUMP_SEQUENCE.md](DUMP_SEQUENCE.md)). [HW] |

These source claims are unverified [INF]:

- write refuses a radio whose serial differs from the loaded data;
- the dongle-gated "Program Default Radio Configuration Data" menu;
- the timing figures (121 ms inter-byte gap, 200 ms ACK timeout).
