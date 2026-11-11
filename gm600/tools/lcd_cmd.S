; lcd_cmd.S — GM600 RAM stub: drive the LCD segments (and backlight) from host-supplied data.
;
; Uploaded with SBEP 0x17, launched with 0x26 (like led_cmd.S). Each cycle: read 21 bytes from the
; SCI into the LCD buffer 0x37AC, force the backlight on (0x34AE bit7, pushed via entry 10), and push
; the LCD buffer (entry 8). Lets the host set any segment pattern to map which bit = which segment.
; Transflective LCD: an energized segment goes DARK on the green backlight. Power-cycle to stop.

        .org    $2000

COPRST  = $103A
SCSR    = $1074         ; SCI status 1 (RDRF = 0x20)
SCDR    = $1077
LEDSH   = $34AE         ; LED shadow (bit7 = backlight)
LCDBUF  = $37AC         ; 21-byte LCD segment buffer
PUSH    = $8F1F         ; SPI dispatcher: B=10 push LEDs, B=8 push LCD
ACQ     = $9FFB         ; SPI-bus device acquire (D = device struct ptr) — sets up pins/mux
REL     = $A01A         ; SPI-bus device release
DEV     = $0245         ; the control-head SPI device struct the firmware passes
PTR     = $2080         ; working pointer
CNT     = $2082         ; byte counter

start:  sei
        lds     #$01FF
loop:   jsr     cop
        ldx     #LCDBUF         ; receive 21 bytes -> LCD buffer
        stx     PTR
        ldab    #21
        stab    CNT
rxlp:   jsr     rxb             ; B = next byte
        ldx     PTR
        stab    0,x
        inx
        stx     PTR
        ldab    CNT
        decb
        stab    CNT
        bne     rxlp
        ldaa    #$02            ; D = DEV (0x0245): acquire the SPI bus (firmware's 0x5013 sync)
        ldab    #$45
        jsr     ACQ
        ldaa    #$80            ; backlight on
        staa    LEDSH
        ldab    #10
        jsr     PUSH
        ldab    #8              ; push the LCD buffer
        jsr     PUSH
        ldaa    #$02            ; D = DEV: release (firmware's 0x5032)
        ldab    #$45
        jsr     REL
        bra     loop

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
