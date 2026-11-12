; asfic_push.S — GM600 NON-halting RAM stub: (optionally) set GCB1 and push the ASFIC image now.
;
; 0x37AC is the 21-byte ASFIC programming image (SPI dispatcher entry 8, decoder select code 2). The
; firmware sends it only when it changes something itself. Byte 8 holds the general-control bits,
; bit n = GCBn: bit 0 amp mute, bit 1 accessory pin 4 (EXTERNAL_ALARM), bit 2 the POWER LATCH (never
; touched here), bit 3 pin 12.
;
; Parameter (written by the host with 0x17 before each 0x26 launch):
;   GCB1REQ  0 = clear GCB1, 1 = set GCB1, bit 7 set = leave the image alone (plain push)
; The read-modify-write of byte 8 runs with interrupts off, so it can't interleave with the firmware's
; own updates of that byte (e.g. the amp-mute bit). Then the stub takes the SPI bus the way the
; firmware's display code does (0x5013 acquire / 0x5032 release, page 4), calls entry 8, and returns
; into the firmware through the 0x26 handler's epilogue (like keyevt.S). The ASFIC keeps the pushed
; value until the firmware's next push.

        .org    $3DC0           ; resident next to keyevt.S (0x3D80-0x3DB8)

BANK    = $1058
PAGE4   = $10           ; bank-register value for page 4 (page = value >> 2)
GCBYTE  = $37B4         ; 0x37AC + 8: the GCB byte of the ASFIC image
GCB1REQ = $3F02
SPIACQ  = $5013         ; page 4: acquire the SPI bus (task-owned lock)
SPIREL  = $5032         ; page 4: release it
SPIDISP = $8F1F         ; fixed bank: SPI dispatcher, B = entry
EPILOG  = $4CE6         ; page 1: the 0x26 handler's own exit (pulx; rts)

start:  ldx     #start          ; self-check (Fletcher-style): a damaged copy spins instead of
        clra                    ; running inside the live firmware; the COP watchdog then resets
        clrb
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
selfok: ldab    GCB1REQ
        bmi     push            ; bit 7: plain push
        sei                     ; atomic read-modify-write of the GCB byte
        ldaa    GCBYTE
        anda    #$FD            ; clear GCB1 only
        andb    #$01
        aslb                    ; requested value -> bit 1
        aba
        staa    GCBYTE
push:   cli                     ; a task normally runs with interrupts on
        ldaa    BANK            ; save the bank (page 1 while the SBEP handler runs)
        psha
        ldaa    #PAGE4
        staa    BANK
        jsr     SPIACQ
        ldab    #8              ; entry 8: 21 bytes from 0x37AC to the ASFIC
        jsr     SPIDISP
        jsr     SPIREL
        pula
        staa    BANK            ; back to the handler's page
        jmp     EPILOG          ; pulx; rts - return to the SBEP dispatcher

        .cksum16                ; zeroes both self-check sums
codeend:
