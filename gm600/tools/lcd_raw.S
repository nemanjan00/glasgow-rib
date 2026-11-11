; lcd_raw.S — GM600 RAM stub: drive the display controller directly, with NO firmware calls.
;
; The display controller is a start-bit-framed serial driver (consistent with an MC145453-class
; 33-segment LCD driver): it ignores 0 bits until a 1 (the start bit), then takes the next 33 bits
; as segment data and latches them. A partial frame survives chip-select, so every push starts with
; 5 zero bytes (40 clocks) to flush one; the firmware does the same. See images/lcd_segments/.
;
; Wire sequence (as firmware entry 5, 0x9018, verified in emu6811):
;   SPCR &= 0xF3; SPCR = 0x52; 0x107E |= 0x61 (select); 10 bytes out; 0x107E &= 0x9E; SPCR |= 0x0C.
; LED shift register (backlight = bit 7), as entry 10 (0x8F64):
;   SPCR = 0x51; 1 byte out; strobe 0x107E |= 0x21; short delay; 0x107E &= 0x9E.
;
; Host protocol (all over the SBEP line; the stub's replies echo back into its own RX and are skipped
; because they are not sync bytes):
;   A5 <10 wire bytes> <cks>   display frame (normally 5 x 00 + start bit + 33 data bits + 6 x 0)
;   5A <1 LED byte>    <cks>   LED shift register
;   cks = 0xFF - sum(sync + payload), so the whole command sums to 0xFF (SBEP convention).
; Reply, one byte: 06 = done, 15 = bad checksum (nothing sent), 54 ('T') = SPI timeout (SPIF never
; set within ~256 polls; select released). Uploaded with SBEP 0x17, launched with 0x26. Halts the
; firmware: power-cycle (psu_reboot.py) to stop.

        .org    $3E40           ; above the firmware's last RAM variables (0x3D66-0x3D7D); 0x3D80-
;                                 0x3FFF reads zero in every RAM dump and nothing references it.
;                                 (0x2000 is the firmware's message-buffer pool: a 255-byte upload
;                                 there crashed the radio mid-upload.)

COPRST  = $103A
SCSR    = $1074         ; SCI status 1 (TDRE = bit7, RDRF = bit5)
SCDR    = $1077         ; SCI data
SPCR    = $1028
SPSR    = $1029         ; SPIF = bit7
SPDR    = $102A
CSPORT  = $107E         ; control-head selects
BUF     = $3F80         ; the 10 wire bytes
BUFEND  = $3F8A
SUM     = $3F8B         ; running checksum
LEDB    = $3F8C         ; LED byte

start:  sei
        ldx     #start          ; self-check: the image (incl. its .cksum byte) must sum to 0 -
        clra                    ; if the firmware overwrote any of it after the host verified it,
selfck: ldab    0,x             ; don't run it
        aba
        inx
        cpx     #codeend
        bne     selfck
        tsta
        beq     selfok
damaged: bra    damaged         ; no COP kick -> watchdog reset -> the radio reboots (safe)
selfok:
        lds     #$3FFF

loop:   jsr     rxb             ; wait for a sync byte; anything else is skipped
        cmpb    #$5A
        beq     led
        cmpb    #$A5
        bne     loop
        stab    SUM
        ldx     #BUF
rd:     jsr     rxsum
        stab    0,x
        inx
        cpx     #BUFEND
        bne     rd
        jsr     rxsum           ; checksum byte
        ldaa    SUM
        cmpa    #$FF
        bne     badck

        ldaa    SPCR            ; dispatcher prologue: clear CPOL|CPHA
        anda    #$F3
        staa    SPCR
        ldaa    #$52            ; SPI master, mode/rate as entry 5
        staa    SPCR
        ldaa    CSPORT          ; assert the display select (RMW, keep other lines)
        oraa    #$61
        staa    CSPORT
        ldx     #BUF
send:   ldaa    0,x
        jsr     spix
        bne     spito
        inx
        cpx     #BUFEND
        bne     send
        jsr     unsel
        ldab    #$06
        jsr     txb
        jmp     loop

led:    stab    SUM
        jsr     rxsum
        stab    LEDB
        jsr     rxsum
        ldaa    SUM
        cmpa    #$FF
        bne     badck
        ldaa    SPCR            ; dispatcher prologue
        anda    #$F3
        staa    SPCR
        ldaa    #$51
        staa    SPCR
        ldaa    LEDB
        jsr     spix
        bne     spito
        ldaa    CSPORT          ; latch strobe
        oraa    #$21
        staa    CSPORT
        ldab    #8              ; stands in for the firmware's fdiv (~41 cycles)
ldly:   decb
        bne     ldly
        jsr     unsel
        ldab    #$06
        jsr     txb
        jmp     loop

badck:  ldab    #$15
        jsr     txb
        jmp     loop

spito:  jsr     unsel
        ldab    #$54
        jsr     txb
        jmp     loop

; spix: send A over SPI, wait for SPIF with a limit. Returns Z=1 on success, Z=0 on timeout.
spix:   staa    SPDR
        clrb                    ; 256 polls
spiw:   ldaa    SPSR
        bmi     spiok
        decb
        bne     spiw
        ldaa    #1              ; Z=0: timeout
        rts
spiok:  ldaa    SPDR            ; clears SPIF
        clra                    ; Z=1: ok
        rts

unsel:  ldaa    CSPORT          ; release the selects
        anda    #$9E
        staa    CSPORT
        ldaa    SPCR            ; dispatcher epilogue: CPOL|CPHA back on
        oraa    #$0C
        staa    SPCR
        rts

rxsum:  jsr     rxb             ; B = next byte; SUM += B
        ldaa    SUM
        aba
        staa    SUM
        rts

rxb:    jsr     cop
        ldaa    SCSR
        anda    #$20            ; RDRF
        beq     rxb
        ldab    SCDR
        rts

txb:    ldaa    SCSR            ; wait TDRE, send B
        bpl     txb
        stab    SCDR
        rts

cop:    ldaa    #$55
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts

        .cksum                  ; balances the image to 0 (mod 256) for the self-check above
codeend:
