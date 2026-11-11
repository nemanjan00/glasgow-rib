; flashtool.S — resident GM600 RAM stub: AT29C020 probe / read / program over the SCI.
;
; Like ramdump.S this is uploaded to RAM with SBEP `17` and launched with the `26` backdoor, but it
; does NOT stream-and-halt: it sits in a command loop (interrupts off) and serves one-byte commands
; from the host until power-off. See ../FLASH_WRITE.md for the AT29C020 command set and the
; device-address -> bank/window mapping used here, and the AT29C020 / 68HC11K4 datasheets under
; datasheets/.
;
;   'I' (0x49) PROBE : AT29C020 software product ID + boot-block lockout. Replies 4 bytes:
;                      manufacturer (expect 1F), device (expect DA), lower-block lockout
;                      (FE=programmable / FF=locked), upper-block lockout. No data writes -> SAFE.
;   'R' (0x52) READ  : host sends page, winhi, winlo (a 256-byte-aligned window addr 0x4000-0x7F00);
;                      replies the 256 bytes of that sector.
;   'W' (0x57) WRITE : host sends page, winhi, winlo, then 256 data bytes; the stub issues the SDP
;                      program prefix, loads the sector, waits tWC, and replies status byte 0x84.
;
; The bus is single-wire half-duplex, so every byte the stub transmits echoes back onto its own RX;
; txb drains that echo. The host (flashtool.py) reads back each written sector to verify.
; Far control transfers use jsr/jmp (branches only reach +/-128); only tight local loops are branches.
; DANGER: `26` runs this with interrupts off; only ever launch the address you just uploaded.

        .org    $2000

SCSR    = $1074         ; SCI status 1 (RDRF = bit5 0x20, TDRE = bit7 0x80)
SCDR    = $1077         ; SCI data
BANK    = $1058         ; bank register = page << 2
COPRST  = $103A         ; COP kick = write 0x55 then 0xAA

CNT16   = $2280         ; loop counter (16-bit, saved across X reuse)
WINPTR  = $2282         ; working window pointer (hi)
WINLO   = $2283         ; working window pointer (lo)
BUFPTR  = $2284         ; working buffer pointer
BUF     = $2300         ; 256-byte sector buffer

; ------------------------------------------------------------------ command loop
start:  sei
loop:   jsr     cop
        jsr     rxb             ; B = command byte
        cmpb    #$49            ; 'I'
        bne     n1
        jmp     do_id
n1:     cmpb    #$52            ; 'R'
        bne     n2
        jmp     do_read
n2:     cmpb    #$57            ; 'W'
        bne     loop
        jmp     do_write

; ------------------------------------------------------------------ helpers
cop:    ldaa    #$55            ; COP watchdog kick
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts

rxb:    jsr     cop             ; wait for a received byte -> B (host->radio, no echo)
        ldaa    SCSR
        anda    #$20            ; RDRF
        beq     rxb
        ldab    SCDR
        rts

txb:    ldaa    SCSR            ; send B, then swallow our own single-wire echo
        anda    #$80            ; TDRE
        beq     txb
        stab    SCDR
txe:    ldaa    SCSR
        anda    #$20            ; RDRF = the echo of what we just sent
        beq     txe
        ldaa    SCDR            ; discard it
        rts

; ------------------------------------------------------------------ AT29C020 product-ID commands
id_cmd: ldab    #4              ; entry: AA->5555 (pg1), 55->2AAA (pg0), 90->5555 (pg1)
        stab    BANK
        ldaa    #$AA
        staa    $5555
        clrb
        stab    BANK
        ldaa    #$55
        staa    $6AAA
        ldab    #4
        stab    BANK
        ldaa    #$90
        staa    $5555
        rts

id_exit: ldab   #4              ; exit: AA->5555, 55->2AAA, F0->5555
        stab    BANK
        ldaa    #$AA
        staa    $5555
        clrb
        stab    BANK
        ldaa    #$55
        staa    $6AAA
        ldab    #4
        stab    BANK
        ldaa    #$F0
        staa    $5555
        rts

; ------------------------------------------------------------------ 'I' probe (ID + lockout)
do_id:  jsr     id_cmd
        clrb
        stab    BANK            ; page 0
        ldab    $4000           ; manufacturer (device 0x0000)
        jsr     txb
        ldab    $4001           ; device (0x0001)
        jsr     txb
        ldab    $4002           ; lower boot-block lockout (0x0002)
        jsr     txb
        ldab    #$3C            ; page 15 (<<2) for device 0x3FFF2
        stab    BANK
        ldab    $7FF2           ; upper boot-block lockout
        jsr     txb
        jsr     id_exit
        jmp     loop

; ------------------------------------------------------------------ 'R' read sector -> host
do_read: jsr    rxb             ; page
        stab    BANK
        jsr     rxb             ; window hi
        stab    WINPTR
        jsr     rxb             ; window lo
        stab    WINLO
        ldx     #256
rdlp:   stx     CNT16
        ldx     WINPTR
        ldab    0,x
        inx
        stx     WINPTR
        jsr     txb
        ldx     CNT16
        dex
        bne     rdlp
        jmp     loop

; ------------------------------------------------------------------ 'W' program sector
do_write:
        jsr     rxb             ; page
        pshb
        jsr     rxb             ; window hi
        stab    WINPTR
        jsr     rxb             ; window lo
        stab    WINLO
        ldx     #BUF            ; receive 256 data bytes into BUF
        stx     BUFPTR
        ldx     #256
wrxlp:  stx     CNT16
        jsr     rxb
        ldx     BUFPTR
        stab    0,x
        inx
        stx     BUFPTR
        ldx     CNT16
        dex
        bne     wrxlp
        ldab    #4              ; SDP program prefix: AA->5555, 55->2AAA, A0->5555
        stab    BANK
        ldaa    #$AA
        staa    $5555
        clrb
        stab    BANK
        ldaa    #$55
        staa    $6AAA
        ldab    #4
        stab    BANK
        ldaa    #$A0
        staa    $5555
        pulb                    ; target page
        stab    BANK
        ldx     #BUF            ; copy BUF -> window (the 256-byte sector load)
        stx     BUFPTR
        ldx     #256
wcplp:  stx     CNT16
        jsr     cop
        ldx     BUFPTR
        ldab    0,x
        inx
        stx     BUFPTR
        ldx     WINPTR
        stab    0,x
        inx
        stx     WINPTR
        ldx     CNT16
        dex
        bne     wcplp
        ldaa    #30             ; wait out tWC (>=15 ms), kicking the COP
wdo:    jsr     cop
        ldx     #2000
wdi:    dex
        bne     wdi
        deca
        bne     wdo
        ldab    #$84            ; status
        jsr     txb
        jmp     loop
