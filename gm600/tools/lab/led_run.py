import asm6811, sbep
a = asm6811.assemble_file("led_sweep.S"); org, code = a.org, a.code
with sbep.Radio() as r:
    print("serial:", r.id_string(1))
    for off in range(0, len(code), 32):
        addr = org + off
        st, _, _ = r.transact(sbep.encode(0x17, addr.to_bytes(3,"big") + code[off:off+32]))
        assert st == sbep.ACK, (hex(addr), st)
    back = b"".join(r.read_block(org+o, min(64, len(code)-o)) for o in range(0, len(code), 64))
    assert back == code, "verify mismatch"
    r.drain(); r.sock.sendall(sbep.encode(0x26, org.to_bytes(2,"big")))
    print("launched - LEDs sweeping (firmware halted); power-cycle to stop")
