"""A tiny 68HC11 assembler — just the subset the GM600 RAM stubs need.

Enough to assemble ``ramdump.S``: labels, ``NAME = expr`` equates, ``.org``, ``.cksum`` (one byte
that makes the whole image sum to 0 mod 256, for a stub's self-check), and a dozen
mnemonics across the inherent / immediate / extended / indexed / relative addressing modes. Not a
general assembler; it exists so the stub can live as readable ``.S`` source with no external
toolchain. Use it as a module (``assemble_file``) or a CLI:

    python3 asm6811.py ramdump.S            # print org + hex
    python3 asm6811.py ramdump.S out.bin    # also write the raw bytes
"""
import re
import sys
from collections import namedtuple

# Per mnemonic: {addressing-mode: opcode}. Modes:
#   INH   inherent (no operand)         IMM8/IMM16  #immediate
#   EXT   extended (16-bit address)     IDX         n,x (X-indexed)
#   REL   8-bit relative branch
INSTR = {
    "sei": {"INH": 0x0F}, "cli": {"INH": 0x0E}, "nop": {"INH": 0x01},
    "inx": {"INH": 0x08}, "dex": {"INH": 0x09},
    "clra": {"INH": 0x4F}, "clrb": {"INH": 0x5F}, "inca": {"INH": 0x4C}, "incb": {"INH": 0x5C},
    "deca": {"INH": 0x4A}, "decb": {"INH": 0x5A}, "tsta": {"INH": 0x4D}, "tstb": {"INH": 0x5D},
    "asla": {"INH": 0x48}, "aslb": {"INH": 0x58}, "lsra": {"INH": 0x44}, "lsrb": {"INH": 0x54},
    "rola": {"INH": 0x49}, "rolb": {"INH": 0x59}, "rora": {"INH": 0x46}, "rorb": {"INH": 0x56},
    "coma": {"INH": 0x43}, "comb": {"INH": 0x53}, "tab": {"INH": 0x16}, "tba": {"INH": 0x17},
    "aba": {"INH": 0x1B},
    "ldaa": {"IMM8": 0x86, "EXT": 0xB6, "IDX": 0xA6},
    "ldab": {"IMM8": 0xC6, "EXT": 0xF6, "IDX": 0xE6},
    "staa": {"EXT": 0xB7, "IDX": 0xA7},
    "stab": {"EXT": 0xF7, "IDX": 0xE7},
    "anda": {"IMM8": 0x84, "EXT": 0xB4},
    "oraa": {"IMM8": 0x8A, "EXT": 0xBA},
    "adda": {"IMM8": 0x8B}, "addb": {"IMM8": 0xCB},
    "cmpa": {"IMM8": 0x81}, "cmpb": {"IMM8": 0xC1},
    "ldx": {"IMM16": 0xCE, "EXT": 0xFE}, "stx": {"EXT": 0xFF}, "cpx": {"IMM16": 0x8C},
    "lds": {"IMM16": 0x8E, "EXT": 0xBE},
    "jmp": {"EXT": 0x7E, "IDX": 0x6E}, "jsr": {"EXT": 0xBD, "IDX": 0xAD}, "rts": {"INH": 0x39},
    "psha": {"INH": 0x36}, "pula": {"INH": 0x32}, "pshb": {"INH": 0x37}, "pulb": {"INH": 0x33},
    "pshx": {"INH": 0x3C}, "pulx": {"INH": 0x38}, "des": {"INH": 0x34}, "ins": {"INH": 0x31},
    "bra": {"REL": 0x20}, "bsr": {"REL": 0x8D}, "beq": {"REL": 0x27}, "bne": {"REL": 0x26},
    "bcs": {"REL": 0x25}, "bcc": {"REL": 0x24}, "bmi": {"REL": 0x2B}, "bpl": {"REL": 0x2A},
}
SIZE = {"INH": 1, "IMM8": 2, "IDX": 2, "REL": 2, "EXT": 3, "IMM16": 3}

Assembled = namedtuple("Assembled", "org code symbols")


class AsmError(Exception):
    pass


def _value(tok, sym):
    """Resolve a numeric token: ``$hex``, ``%bin``, decimal, or a defined symbol name."""
    tok = tok.strip()
    if tok.startswith("#"):
        tok = tok[1:].strip()
    if tok.startswith("$"):
        return int(tok[1:], 16)
    if tok.startswith("%"):
        return int(tok[1:], 2)
    if re.match(r"^[A-Za-z_]\w*$", tok):
        if tok not in sym:
            raise AsmError(f"undefined symbol: {tok}")
        return sym[tok]
    return int(tok, 0)


def _mode(mnemonic, operand):
    modes = INSTR[mnemonic]
    if operand is None:
        return "INH"
    if operand.startswith("#"):
        return "IMM16" if "IMM16" in modes else "IMM8"
    if re.match(r"^.+,[xX]$", operand):
        return "IDX"
    return "REL" if "REL" in modes else "EXT"


def assemble(source, symbols=None):
    """Assemble `source` to an :class:`Assembled` (org, bytes, final symbol table)."""
    sym = dict(symbols or {})
    org = 0
    insns = []            # (addr, mnemonic, operand)
    addr = 0
    seen_org = False

    for lineno, raw in enumerate(source.splitlines(), 1):
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        try:
            # label in column 0 (optional trailing colon)
            label = None
            if not line[0].isspace():
                label, _, line = line.partition(" ")
                label = label.rstrip(":")
            toks = line.split()

            if toks and toks[0] == ".org":
                addr = org = _value(toks[1], sym)
                seen_org = True
                if label:
                    sym[label] = addr
                continue
            # equate:  NAME = expr   (NAME came through as the label, or is toks[0])
            if "=" in line.replace("==", ""):
                name = label or toks[0]
                rhs = line.split("=", 1)[1]
                sym[name] = _value(rhs.strip(), sym)
                continue
            if label:
                sym[label] = addr
            if not toks:
                continue
            if toks[0] == ".cksum":
                insns.append((addr, ".cksum", None))
                addr += 1
                continue
            mnemonic, operand = toks[0], (toks[1] if len(toks) > 1 else None)
            if mnemonic not in INSTR:
                raise AsmError(f"unknown mnemonic: {mnemonic}")
            insns.append((addr, mnemonic, operand))
            addr += SIZE[_mode(mnemonic, operand)]
        except AsmError as e:
            raise AsmError(f"line {lineno}: {e}  |  {raw.strip()}")

    if not seen_org:
        raise AsmError("no .org directive")

    out = bytearray()
    cksum_at = None
    for at, mnemonic, operand in insns:
        if mnemonic == ".cksum":
            cksum_at = len(out)
            out.append(0)
            continue
        mode = _mode(mnemonic, operand)
        op = INSTR[mnemonic][mode]
        if mode == "INH":
            out.append(op)
        elif mode == "IMM8":
            out += bytes([op, _value(operand, sym) & 0xFF])
        elif mode in ("IMM16", "EXT"):
            v = _value(operand, sym) & 0xFFFF
            out += bytes([op, v >> 8, v & 0xFF])
        elif mode == "IDX":
            out += bytes([op, _value(operand.split(",")[0], sym) & 0xFF])
        elif mode == "REL":
            rel = _value(operand, sym) - (at + 2)
            if not -128 <= rel <= 127:
                raise AsmError(f"branch out of range at 0x{at:04X}: {mnemonic} {operand}")
            out += bytes([op, rel & 0xFF])
    if cksum_at is not None:                      # make the whole image sum to 0 (mod 256)
        out[cksum_at] = -sum(out) & 0xFF
    return Assembled(org, bytes(out), sym)


def assemble_file(path, symbols=None):
    with open(path) as f:
        return assemble(f.read(), symbols)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} file.S [out.bin]")
    a = assemble_file(sys.argv[1])
    print(f"org 0x{a.org:04X}  {len(a.code)} bytes")
    print(a.code.hex())
    if len(sys.argv) > 2:
        with open(sys.argv[2], "wb") as f:
            f.write(a.code)
