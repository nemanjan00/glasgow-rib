import asm6811, sbep, time, subprocess, sys
def cap(n):
    subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -video_size 1280x720 -i /dev/video2 -vframes 12 -update 1 -y /tmp/%s_raw.jpg"%n,shell=True)
    subprocess.run("magick /tmp/%s_raw.jpg -crop 300x150+660+190 +repage -resize 300x /tmp/%s.jpg"%(n,n),shell=True)
a=asm6811.assemble_file("lcd_arb.S"); org,code=a.org,a.code
with sbep.Radio() as r:
    print("serial:",r.id_string(1))
    live=bytes(r.read_block(0x37AC,21)); print("live 322 buf:",live.hex())
    for off in range(0,len(code),32):
        st,_,_=r.transact(sbep.encode(0x17,(org+off).to_bytes(3,"big")+code[off:off+32])); assert st==sbep.ACK
    back=b"".join(r.read_block(org+o,min(64,len(code)-o)) for o in range(0,len(code),64)); assert back==code,"upload mismatch"
    r.drain(); r.sock.sendall(sbep.encode(0x26,org.to_bytes(2,"big"))); time.sleep(0.8)
    def frame(buf,s=0.35):
        r.sock.sendall(bytes(buf)); time.sleep(s)
        try:
            r.sock.settimeout(0.1)
            while r.sock.recv(256): pass
        except: pass
    def show(buf,nm):
        frame(buf); frame(buf); frame(buf); time.sleep(0.3); cap(nm); print(nm,"pushed")
    show(live,"a_live")                       # should reproduce 322
    show(bytes([0]*21),"a_blank")             # should blank
    show(bytes([0xFF]*21),"a_full")           # all segments
    frame(live)
