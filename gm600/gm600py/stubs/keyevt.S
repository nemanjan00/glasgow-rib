; keyevt.S — GM600 NON-halting RAM stub: post one keypad event into the running firmware.
;
; Launched with the firmware's own SBEP 0x26 handler (CPU 0x4CDC in page 1: pshb/psha, sei, jump to
; the request's address) and it RETURNS into the firmware afterwards, so the radio keeps running.
; It calls the keypad task's own event routine 0x4D8B (page 0) with D = event number and a 16-bit
; stack argument = state (1 pressed, 0 released) — exactly how the keypad task reports a key each
; scan (event 20 = button A; map in ../FIRMWARE.md). 0x4D8B records the state at 0x3460+event and,
; on a change, posts the key message to the UI task.
;
; Parameters live in RAM outside the code (written by the host with 0x17 before each launch):
;   EVT   event number (1 byte)          STATE  0 = released, 1 = pressed (1 byte)
;
; Used with the keypad task paused (its TCB+8 at 0x11D8 set to 0x90) so the next real scan doesn't
; immediately report the key released; see keyhold.py.

        .org    $3D80

BANK    = $1058
EVT     = $3F00
STATE   = $3F01
KEYEVT  = $4D8B         ; page 0: report key event (D = event, stack arg = state)
EPILOG  = $4CE6         ; page 1: the 0x26 handler's own exit (pulx; rts)

start:  ldx     #start          ; self-check (Fletcher-style, like the other stubs): a damaged copy
        clra                    ; must not run inside the live firmware - spin instead, the COP
        clrb                    ; watchdog then resets the radio (safe)
selfck: adda    0,x
        psha
        aba
        tab
        pula
        inx
        cpx     #codeend
        bne     selfck
        tsta
        bne     damaged
        tstb
        beq     selfok
damaged: bra    damaged
selfok: cli                     ; a task normally runs with interrupts on
        ldaa    BANK            ; save the bank (page 1 while the SBEP handler runs)
        psha
        clra
        staa    BANK            ; page 0 for 0x4D8B
        ldab    STATE
        clra
        pshb                    ; 16-bit state argument (low byte pushed first)
        psha
        ldab    EVT
        clra                    ; D = event
        jsr     KEYEVT
        pulx                    ; drop the argument
        pula
        staa    BANK            ; back to the handler's page
        jmp     EPILOG          ; pulx; rts - return to the SBEP dispatcher

        .cksum16                ; zeroes both self-check sums
codeend:
