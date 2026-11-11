import asm6811, sbep, time, subprocess
def cap(n): subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -i /dev/video2 -vframes 8 -update 1 -y /tmp/%s.jpg"%n,shell=True)
INIT = [
 (0x20,0x50,"ffffff7e12c0060513fcb92187"),(0x20,0x50,"ffffff7e12c0060513fcb92187"),
 (0x01,0x50,"222100c50070c980c5affe00000000000000210000"),(0x01,0x50,"022106c50070c980c4"),
 (0x61,0x52,"0000000000fffffeffc0"),(0x60,0x51,"de"),(0x60,0x51,"df"),
 (0x01,0x50,"022100c50070c980c5"),
]
def phase(r,cs,spcr,b):
    r.sock.sendall(bytes([cs,spcr,len(b)])+bytes(b)); time.sleep(0.02)
    try:
        r.sock.settimeout(0.04)
        while r.sock.recv(256): pass
    except Exception: pass
def show(r,data21):
    phase(r,0x41,0x50,bytes.fromhex("00003f"))   # address cmd
    phase(r,0x01,0x50,data21)                     # data
a=asm6811.assemble_file("lcd_replay.S"); org,code=a.org,a.code
with sbep.Radio() as r:
    print("serial:", r.id_string(1))
    for off in range(0,len(code),32):
        st,_,_=r.transact(sbep.encode(0x17,(org+off).to_bytes(3,"big")+code[off:off+32])); assert st==sbep.ACK
    back=b"".join(r.read_block(org+o,min(64,len(code)-o)) for o in range(0,len(code),64)); assert back==code
    r.drain(); r.sock.sendall(sbep.encode(0x26,org.to_bytes(2,"big"))); time.sleep(0.5)
    for cs,spcr,d in INIT: phase(r,cs,spcr,bytes.fromhex(d))
    show(r,bytes([0x00]*21)); time.sleep(0.25); cap("seg_00")
    show(r,bytes([0xFF]*21)); time.sleep(0.25); cap("seg_FF")
    for n in range(21):
        buf=bytearray(21); buf[n]=0xFF
        show(r,bytes(buf)); time.sleep(0.2); cap("seg_b%02d"%n)
    print("swept")
print("done - power-cycle after")
