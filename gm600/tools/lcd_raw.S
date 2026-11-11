; lcd_raw.S — GM600 RAM stub: drive the 3-digit 7-seg controller directly, with NO firmware calls.
;
; Same job as lcd_text.S, but instead of calling SPI-dispatcher entry 5 (0x9018 / 0x9102) it does the
; SPI itself, replicating what entry 5 puts on the wire (verified in emu6811 against the firmware):
;   SPCR &= 0xF3; SPCR = 0x52; 0x107E |= 0x61 (chip select); send 5 x 0x00, then the 5 packed frame bytes;
;   0x107E &= ~0x61; SPCR |= 0x0C (the dispatcher's epilogue).
; The host supplies all 10 wire bytes: normally 5 x 00 + lcd_text.pack(struct) (a port of 0x9018),
; but any bytes can be sent, which reaches segments the firmware's packing never sets.
;
; Command loop: 0xA5 + 10 wire bytes -> display controller; 0x5A + 1 byte -> LED shift register
; (bit 7 = LCD/keypad backlight; see FIRMWARE.md LED map). Uploaded with
; SBEP 0x17, launched with 0x26. Halts the firmware: power-cycle (psu_reboot.py) to stop.

        .org    $2000

COPRST  = $103A
SCSR    = $1074         ; SCI status 1 (RDRF = bit5 = 0x20)
SCDR    = $1077         ; SCI data
SPCR    = $1028
SPSR    = $1029         ; SPIF = bit7
SPDR    = $102A
CSPORT  = $107E         ; control-head selects
BUF     = $2100         ; the 10 wire bytes (entry 5 sends 5 x 00 + the 5 packed bytes)
BUFEND  = $210A

start:  sei
        lds     #$21FF

loop:   jsr     rxb             ; sync: 0xA5 = digits, 0x5A = LEDs (anything else is skipped)
        cmpb    #$5A
        beq     led
        cmpb    #$A5
        bne     loop
        ldx     #BUF
rd:     jsr     rxb
        stab    0,x
        inx
        cpx     #BUFEND
        bne     rd

        ldaa    SPCR            ; dispatcher prologue: clear CPOL|CPHA
        anda    #$F3
        staa    SPCR
        ldaa    #$52            ; SPI master, mode/rate as entry 5
        staa    SPCR
        ldaa    CSPORT          ; assert the 7-seg controller select (RMW, keep other lines)
        oraa    #$61
        staa    CSPORT
        ldx     #BUF
send:   ldaa    0,x
        staa    SPDR
wait:   ldaa    SPSR
        bpl     wait            ; SPIF
        ldaa    SPDR            ; clears SPIF
        inx
        cpx     #BUFEND
        bne     send
        ldaa    CSPORT          ; release
        anda    #$9E
        staa    CSPORT
        ldaa    SPCR            ; dispatcher epilogue: CPOL|CPHA back on
        oraa    #$0C
        staa    SPCR
        bra     loop

; LED shift register (front-panel LEDs + LCD backlight bit 7), as firmware entry 10 (0x8F64):
; SPCR = 0x51, shift one byte out (no select), strobe 0x107E |= 0x21, short delay, clear 0x61.
led:    jsr     rxb
        ldaa    SPCR            ; dispatcher prologue
        anda    #$F3
        staa    SPCR
        ldaa    #$51
        staa    SPCR
        stab    SPDR
lwait:  ldaa    SPSR
        bpl     lwait
        ldaa    SPDR
        ldaa    CSPORT          ; latch strobe
        oraa    #$21
        staa    CSPORT
        ldab    #8              ; stands in for the firmware's fdiv (~41 cycles)
ldly:   decb
        bne     ldly
        ldaa    CSPORT
        anda    #$9E
        staa    CSPORT
        ldaa    SPCR            ; dispatcher epilogue
        oraa    #$0C
        staa    SPCR
        jmp     loop

rxb:    jsr     cop
        ldaa    SCSR
        anda    #$20            ; RDRF
        beq     rxb
        ldab    SCDR
        rts

cop:    ldaa    #$55
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts
