import sbep, time, subprocess, threading
pat=[bytes([0x00]*21)]; stop=[False]
def writer():
    with sbep.Radio() as r:
        while not stop[0]:
            try: r.transact(sbep.encode(0x17,(0x37AC).to_bytes(3,"big")+pat[0]),reply=False)
            except Exception: pass
t=threading.Thread(target=writer); t.start()
def cap(n): time.sleep(1.8); subprocess.run("ffmpeg -nostdin -loglevel error -f v4l2 -input_format mjpeg -i /dev/video2 -vframes 10 -update 1 -y /tmp/%s.jpg"%n,shell=True)
pat[0]=bytes([0x00]*21); cap("d_blank")
pat[0]=bytes([0xFF]*21); cap("d_full")
# only the DIGIT bytes FF, keep others 0 -> show 888 w/o extra icons
dig=bytearray(21)
for i in (3,5,6,12,13,14,16,17,18,20): dig[i]=0xFF
pat[0]=bytes(dig); cap("d_dig")
stop[0]=True; t.join(); print("demo done")
