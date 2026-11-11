; lcd_cmd.S — GM600 RAM stub: drive the LCD segments from host-supplied data.
;
; Uploaded with SBEP 0x17, launched with 0x26. The LCD (a segment driver on the control-head SPI
; bus) needs its address pointer set once with a COMMAND before data: the firmware sends command
; "00 00 3f" via entry 3 (CS=0x41, bit6=command) at init, then streams the 21-byte 0x37AC buffer via
; entry 8 (CS=0x01, data) each refresh (the controller auto-wraps after 21 bytes). This stub does the
; same: entry-3 command once at start, then a loop that reads 21 bytes from the SCI into 0x37AC,
; forces the backlight on (entry 10), and pushes the LCD (entry 8). See ../FIRMWARE.md.
; Transflective LCD: an energized segment goes DARK on the green backlight. Power-cycle to stop.

        .org    $2000

COPRST  = $103A
SCSR    = $1074         ; SCI status 1 (RDRF = 0x20)
SCDR    = $1077
LEDSH   = $34AE         ; LED shadow (bit7 = backlight)
LCDBUF  = $37AC         ; 21-byte LCD segment buffer
CMDBUF  = $01EE         ; where entry 3 reads its 3-byte command from
PUSH    = $8F1F         ; SPI dispatcher: B=3 LCD-command, B=8 LCD-data, B=10 LED
PTR     = $2080
CNT     = $2082

start:  sei
        lds     #$01FF
        jsr     cop
        clra                    ; command "00 00 3f" -> 0x01EE..0x01F0, set LCD address pointer
        staa    $01EE
        staa    $01EF
        ldaa    #$3F
        staa    $01F0
        ldab    #3              ; entry 3 = LCD command (CS=0x41)
        jsr     PUSH
loop:   jsr     cop
        ldx     #LCDBUF         ; receive 21 bytes -> LCD buffer
        stx     PTR
        ldab    #21
        stab    CNT
rxlp:   jsr     rxb
        ldx     PTR
        stab    0,x
        inx
        stx     PTR
        ldab    CNT
        decb
        stab    CNT
        bne     rxlp
        ldaa    #$80            ; backlight on (entry 10)
        staa    LEDSH
        ldab    #10
        jsr     PUSH
        ldab    #8              ; LCD data (entry 8)
        jsr     PUSH
        bra     loop

rxb:    jsr     cop
        ldaa    SCSR
        anda    #$20
        beq     rxb
        ldab    SCDR
        rts

cop:    ldaa    #$55
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts
