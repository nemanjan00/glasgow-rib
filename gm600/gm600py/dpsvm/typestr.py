#!/usr/bin/env python3
"""Type a string into the QEMU guest via its monitor socket, mapping to the guest's German QWERTZ
layout. Used to drive WDPS in the Win3.1 VM headlessly.  usage: typestr.py "text" | typestr.py --clear
QEMU monitor socket path defaults to /tmp/qmon; override with the QMON env var."""
import socket, time, sys, os
QMON=os.environ.get("QMON","/tmp/qmon")
# German QWERTZ layout mapping (scancode positions -> desired chars)
MAP={':':'shift-dot','\\':'ctrl-alt-minus','_':'shift-slash','.':'dot',' ':'spc','-':'slash'}
def key(k):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(QMON)
    time.sleep(0.02); s.sendall(("sendkey %s\n"%k).encode()); time.sleep(0.05)
    try:
        s.settimeout(0.1)
        while s.recv(4096): pass
    except Exception: pass
    s.close()
arg=sys.argv[1]
if arg=="--clear":
    key("end")
    for _ in range(45): key("backspace"); time.sleep(0.03)
    print("cleared"); sys.exit()
for ch in arg:
    if ch in MAP: k=MAP[ch]
    elif ch.isalnum(): k=ch.lower()
    else: raise SystemExit("unmapped %r"%ch)
    key(k); time.sleep(0.06)
print("typed:",arg)
