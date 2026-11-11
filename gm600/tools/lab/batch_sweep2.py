import importlib.util, time, subprocess, sys
sys.path.insert(0,"/home/nemanjan00/glasgow-rib/gm600/tools")
spec=importlib.util.spec_from_file_location("sbep","/home/nemanjan00/glasgow-rib/gm600/tools/sbep.py")
sbep=importlib.util.module_from_spec(spec); spec.loader.exec_module(sbep)
import asm6811
a=asm6811.assemble_file("/home/nemanjan00/glasgow-rib/gm600/tools/lab/lcd_segstub.S"); org,base=a.org,bytearray(a.code)
def reboot():
    subprocess.run("cd ~/pymp305/python && ~/pymp305/.venv-hw/bin/python ~/captures/psu_reboot.py 2 >/dev/null 2>&1", shell=True); time.sleep(5)
def run(N):
    reboot()
    code=bytearray(base); code[38]=(0xAC+N)&0xFF
    r=sbep.Radio()
    for off in range(0,len(code),32): r.transact(sbep.encode(0x17,(org+off).to_bytes(3,"big")+bytes(code[off:off+32])))
    r.drain(); r.sock.sendall(sbep.encode(0x26,org.to_bytes(2,"big"))); time.sleep(1.5); r.sock.close()
    dead=True
    try:
        rr=sbep.Radio(); rr.read_block(0x37AC,1); rr.sock.close(); dead=False
    except Exception: dead=True
    time.sleep(0.3)
    subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -video_size 1280x720 -i /dev/video2 -vframes 10 -update 1 -y /tmp/g_%02d_raw.jpg"%N, shell=True)
    subprocess.run("magick /tmp/g_%02d_raw.jpg -crop 320x150+650+185 +repage -resize 200x /tmp/g_%02d.jpg"%(N,N), shell=True)
    print("N=%02d %s"%(N,"RUN(dead)" if dead else "CRASH(alive)"),flush=True)
for N in range(21): run(N)
print("DONE",flush=True)
