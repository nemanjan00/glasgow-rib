; led_cmd.S — GM600 RAM stub: set the LED shadow to a host-supplied byte, on demand.
;
; Uploaded with SBEP 0x17 and launched with 0x26 (like flashtool.S). Command loop: read one byte
; from the SCI, store it to the LED shadow 0x34AE, and call the firmware's LED-push (ldab #10;
; jsr 0x8F1F) so the front-panel LED shift register shows it. The byte persists (the shift register
; latches) until the next byte. Used to map which 0x34AE bit drives which LED. Power-cycle to stop.

        .org    $2000

COPRST  = $103A
SCSR    = $1074         ; SCI status 1 (RDRF = bit5 = 0x20)
SCDR    = $1077         ; SCI data
LEDSH   = $34AE         ; LED shadow byte
PUSH    = $8F1F         ; SPI device dispatcher; B=10 -> push LEDs

start:  sei
        lds     #$01FF
loop:   jsr     cop
        jsr     rxb             ; B = LED value from host
        stab    LEDSH
        ldab    #10
        jsr     PUSH            ; push shadow -> LED shift register
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
