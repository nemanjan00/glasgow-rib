import importlib.util, time, subprocess, os
spec=importlib.util.spec_from_file_location("sbep","/home/nemanjan00/glasgow-rib/gm600/tools/sbep.py")
sbep=importlib.util.module_from_spec(spec); spec.loader.exec_module(spec and sbep)
import sys; sys.path.insert(0,"/home/nemanjan00/glasgow-rib/gm600/tools")
import asm6811
a=asm6811.assemble_file("/home/nemanjan00/glasgow-rib/gm600/tools/lab/lcd_disp4.S"); org,code=a.org,a.code
def reboot():
    subprocess.run("cd ~/pymp305/python && ~/pymp305/.venv-hw/bin/python ~/captures/psu_reboot.py 2 >/dev/null 2>&1", shell=True); time.sleep(5)
def run_buf(buf, nm):
    reboot()
    r=sbep.Radio()
    for off in range(0,len(code),32): r.transact(sbep.encode(0x17,(org+off).to_bytes(3,"big")+code[off:off+32]))
    r.transact(sbep.encode(0x17,(0x2100).to_bytes(3,"big")+bytes(buf)))
    r.drain(); r.sock.sendall(sbep.encode(0x26,org.to_bytes(2,"big"))); time.sleep(0.9); r.sock.close(); time.sleep(0.4)
    subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -video_size 1280x720 -i /dev/video2 -vframes 10 -update 1 -y /tmp/%s_raw.jpg"%nm, shell=True)
    subprocess.run("magick /tmp/%s_raw.jpg -crop 320x150:0:0 2>/dev/null; magick /tmp/%s_raw.jpg -crop 320x150+650+185 +repage -resize 200x /tmp/%s.jpg"%(nm,nm,nm), shell=True)
    print(nm,"done",flush=True)
run_buf(bytes([0xFF]*21),"s_ff")
run_buf(bytes.fromhex("853018c1008fd080e5affe00a26180006018020010"),"s_322")
for N in range(21):
    b=bytearray(21); b[N]=0xFF; run_buf(bytes(b),"s_b%02d"%N)
print("SWEEP COMPLETE",flush=True)
