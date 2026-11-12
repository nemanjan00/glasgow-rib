#!/usr/bin/env python3
"""Minimal read-only SBEP client for the GM600 via the glasgow-rib applet socket.

Queries the serial number (``23 01``) and reads 32 bytes at 0x0F60 (the channel region), printing
what came back. Use ``sbep.Radio`` directly for anything more:

    from gm600py.link.sbep import Radio, encode
    with Radio() as r:
        print(r.id_string(1))
        data = r.read_block(0x0F60, 0x20)
        status, op, data = r.transact(encode(0x13))

usage: sbep_client.py      (SBEP_ADDR=host:port, default 127.0.0.1:2324)
"""
from gm600py.link.sbep import Radio, SBEPError


def main():
    try:
        with Radio() as radio:
            print("serial       :", radio.id_string(1))
            data = radio.read_block(0x0F60, 0x20)
            print("0x0F60 (32 B):", data.hex(" "))
    except SBEPError as e:
        print("failed:", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
