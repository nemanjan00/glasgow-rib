#!/usr/bin/env python3
"""Set the GM600 displayed idle number (MPT1327 Own Unit No.) over SBEP, no DPS needed.

The Own Unit No. is a big-endian 16-bit value in the codeplug at 0x0F0B-0x0F0C (322=0x0142),
protected by the block checksum at 0x0FF4 = (sum(0x0F08..0x0FF3)-0x55)&0xFF. This reads the live
codeplug, patches those two bytes + the checksum, writes the two covering 32-byte chunks via 0x17,
then resets (0x10) so the radio re-renders. Verified on hardware (322/456/712).

  usage: setunit.py <number>      e.g. setunit.py 456
"""
import sys,time,os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbep import Radio, encode
RB=0x0D80
val=int(sys.argv[1])    # new Own Unit No.
with Radio() as r:
    # read current full codeplug
    cp=bytearray()
    for a in range(0x0D80,0x1000,0x20): cp+=r.read_block(a,0x20)
    # patch 0x0F0B-0x0F0C big-endian, fix block 0x0F08-0x0FF4 checksum at 0x0FF4
    cp[0x0F0B-RB]=(val>>8)&0xFF; cp[0x0F0C-RB]=val&0xFF
    s0,ck0=0x0F08-RB,0x0FF4-RB
    cp[ck0]=(sum(cp[s0:ck0])-0x55)&0xFF
    # write two chunks covering 0x0F0B-0x0F0C and 0x0FF4
    for addr in (0x0EFA,0x0FD5):
        chunk=bytes(cp[addr-RB:addr-RB+32])
        status,op,_=r.transact(encode(0x17, addr.to_bytes(3,"big")+chunk))
        print("write 0x%04X:"%addr, "OK" if op==0x84 else "FAIL status %02X op %s"%(status,op))
    # reset
    r.sock.sendall(encode(0x10)); time.sleep(0.4)
print("set Own Unit No. = %d (0x%03X), reset sent"%(val,val))
