#!/usr/bin/env python3
"""Set the GM600 displayed idle number (MPT1327 Own Unit No.) over SBEP, no DPS needed.

The Own Unit No. is a big-endian 16-bit value in the codeplug at 0x0F0B-0x0F0C (322=0x0142),
protected by the block checksum at 0x0FF4 = (sum(0x0F08..0x0FF3)-0x55)&0xFF. This reads the live
codeplug, patches those two bytes + the checksum, writes the two covering 32-byte chunks via 0x17,
then resets (0x10) so the radio re-renders. Verified on hardware (322/456/712).

  usage: setunit.py <number>      e.g. setunit.py 456
"""
import socket,sys,time,os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_codeplug import read_block
RB=0x0D80
def cks(b): return (0xFF-(sum(b)&0xFF))&0xFF
def wframe(addr,data):
    ln=1+3+len(data)
    body=bytes([0xFF,0x17,(ln>>8)&0xFF,ln&0xFF,(addr>>16)&0xFF,(addr>>8)&0xFF,addr&0xFF])+bytes(data)
    return body+bytes([cks(body)])
def short(op,d=b""): b=bytes([0xF0|(1+len(d)),op])+bytes(d); return b+bytes([cks(b)])
def xfer(s,fr):
    s.settimeout(0.05)
    try:
        while s.recv(256): pass
    except: pass
    s.settimeout(2.0); s.sendall(fr); time.sleep(0.3)
    buf=bytearray()
    try:
        while True:
            d=s.recv(256)
            if not d: break
            buf+=d
    except: pass
    return bytes(buf)
val=int(sys.argv[1])    # new Own Unit No.
# read current full codeplug
s=socket.create_connection(("127.0.0.1",2324),timeout=5)
cp=bytearray()
for a in range(0x0D80,0x1000,0x20): cp+=read_block(s,a)
# patch 0x0F0B-0x0F0C big-endian, fix block 0x0F08-0x0FF4 checksum at 0x0FF4
cp[0x0F0B-RB]=(val>>8)&0xFF; cp[0x0F0C-RB]=val&0xFF
s0,ck0=0x0F08-RB,0x0FF4-RB
cp[ck0]=(sum(cp[s0:ck0])-0x55)&0xFF
# write two chunks covering 0x0F0B-0x0F0C and 0x0FF4
for addr in (0x0EFA,0x0FD5):
    chunk=bytes(cp[addr-RB:addr-RB+32])
    rep=xfer(s,wframe(addr,chunk))
    print("write 0x%04X:"%addr, "OK" if 0x84 in rep else "FAIL "+rep.hex())
# reset
s.sendall(short(0x10)); time.sleep(0.4)
s.close()
print("set Own Unit No. = %d (0x%03X), reset sent"%(val,val))
