"""Co-resident OC3-ISR LCD driver (experiment). Keeps 0x37AC = a host-set 21-byte buffer so the
firmware pushes arbitrary content. IRQs stay on (SBEP alive; recover with PSU, no stuck stub).
Stub @0x2000 copies 0x2100->0x37AC (RTI). Hooks the free OC3 pseudo-vector $A4 (handler 0x92A1
clears the flag + jmps $A4); periodic via TOC3=0x0000 (fires each TCNT overflow)."""
import sys, time
sys.path.insert(0, "/home/nemanjan00/captures")   # dump_codeplug for read_block fallback
import importlib.util
spec = importlib.util.spec_from_file_location("sbep", "/home/nemanjan00/glasgow-rib/gm600/tools/sbep.py")
sbep = importlib.util.module_from_spec(spec); spec.loader.exec_module(sbep)

BUF = bytes([0xFF]*21) if len(sys.argv)<2 else bytes.fromhex(sys.argv[1])
assert len(BUF)==21
# stub: 21x (ldaa $21ii ; staa $37jj) ; rti
stub=bytearray()
for i in range(21):
    stub += bytes([0xB6,0x21,i, 0xB7,0x37,0xAC+i])
stub += bytes([0x3B])
def w(r,addr,data):
    st,_,_=r.transact(sbep.encode(0x17,addr.to_bytes(3,"big")+bytes(data))); 
    return st
with sbep.Radio() as r:
    print("serial:", r.id_string(1))
    for off in range(0,len(stub),32): assert w(r,0x2000+off, stub[off:off+32])==sbep.ACK
    back=b"".join(r.read_block(0x2000+o,min(64,len(stub)-o)) for o in range(0,len(stub),64)); assert back==bytes(stub),"stub upload mismatch"
    assert w(r,0x2100, BUF)==sbep.ACK
    assert w(r,0x00A4, bytes([0x20,0x00]))==sbep.ACK      # OC3 pseudo-vector -> 0x2000
    assert w(r,0x101A, bytes([0x00,0x00]))==sbep.ACK      # TOC3 = 0 (fire each overflow)
    tmsk1=r.read_block(0x1022,1)[0]
    assert w(r,0x1022, bytes([tmsk1|0x20]))==sbep.ACK     # enable OC3 interrupt (last)
    print("OC3 ISR armed; TMSK1 %02X->%02X; buffer=%s"%(tmsk1,tmsk1|0x20,BUF.hex()))
    time.sleep(1.0)
    print("0x37AC now:", bytes(r.read_block(0x37AC,21)).hex())
