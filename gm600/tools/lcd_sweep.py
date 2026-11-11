import asm6811, sbep, time, subprocess
def cap(name):
    subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -i /dev/video2 "
                   "-vframes 8 -update 1 -y /tmp/%s.jpg" % name, shell=True)
a = asm6811.assemble_file("lcd_cmd.S"); org, code = a.org, a.code
with sbep.Radio() as r:
    print("serial:", r.id_string(1))
    for off in range(0, len(code), 32):
        st,_,_ = r.transact(sbep.encode(0x17,(org+off).to_bytes(3,"big")+code[off:off+32])); assert st==sbep.ACK
    back=b"".join(r.read_block(org+o,min(64,len(code)-o)) for o in range(0,len(code),64)); assert back==code
    r.drain(); r.sock.sendall(sbep.encode(0x26, org.to_bytes(2,"big"))); time.sleep(0.4)
    def frame(buf):
        r.sock.sendall(bytes(buf)); time.sleep(0.2)
        try:
            r.sock.settimeout(0.1)
            while r.sock.recv(256): pass
        except Exception: pass
    frame([0xFF]*21); cap("all_on");  print("all-on")
    frame([0x00]*21); cap("all_off"); print("all-off (backlight only)")
    for n in range(21):
        buf=[0]*21; buf[n]=0xFF
        frame(buf); cap("byte%02d"%n); print("byte %2d"%n)
    frame([0]*21)
print("done - firmware halted; power-cycle after")
