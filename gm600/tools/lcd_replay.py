import asm6811, sbep, time, subprocess, sys
def cap(n): subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -i /dev/video2 -vframes 8 -update 1 -y /tmp/%s.jpg"%n,shell=True)
# captured firmware init phases: (cs, spcr, data-hex)
INIT = [
 (0x20,0x50,"ffffff7e12c0060513fcb92187"),
 (0x20,0x50,"ffffff7e12c0060513fcb92187"),
 (0x01,0x50,"222100c50070c980c5affe00000000000000210000"),
 (0x01,0x50,"022106c50070c980c4"),
 (0x61,0x52,"0000000000fffffeffc0"),
 (0x60,0x51,"de"),
 (0x60,0x51,"df"),
 (0x01,0x50,"022100c50070c980c5"),
]
ADDR_CMD = (0x41,0x50,"00003f")
# the data pattern to display (firmware's captured phase-9 segments)
DATA_HEX = "003c00f98000d180e5affe00f0c000007b0c1e0010"
def phase(r, cs, spcr, hexdata):
    b=bytes.fromhex(hexdata)
    r.sock.sendall(bytes([cs,spcr,len(b)])+b); time.sleep(0.03)
    try:
        r.sock.settimeout(0.05)
        while r.sock.recv(256): pass
    except Exception: pass
a=asm6811.assemble_file("lcd_replay.S"); org,code=a.org,a.code
with sbep.Radio() as r:
    print("serial:", r.id_string(1))
    for off in range(0,len(code),32):
        st,_,_=r.transact(sbep.encode(0x17,(org+off).to_bytes(3,"big")+code[off:off+32])); assert st==sbep.ACK
    back=b"".join(r.read_block(org+o,min(64,len(code)-o)) for o in range(0,len(code),64)); assert back==code
    r.drain(); r.sock.sendall(sbep.encode(0x26,org.to_bytes(2,"big"))); time.sleep(0.5)
    # full init once
    for cs,spcr,d in INIT: phase(r,cs,spcr,d)
    # then address cmd + data, a few times (refresh)
    for _ in range(4):
        phase(r,*ADDR_CMD); phase(r,0x01,0x50,DATA_HEX)
    time.sleep(0.3); cap("replay"); print("replayed full init + data")
print("done - power-cycle after")
