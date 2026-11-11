; lcd_replay.S — GM600 RAM stub: replay host-supplied SPI phases to the control head verbatim.
;
; Each phase from the host over the SCI is [cs][spcr][nlen][nlen data bytes]. The stub sets SPCR,
; asserts the CS/mode bits on port 0x107E, streams the bytes via the firmware's SPI routine 0x9102,
; then clears 0x107E bits 0/5/6 — exactly what the firmware's device entries do. This lets the host
; replay the firmware's real LCD init+data sequence (captured in the emulator) byte-for-byte, with no
; assumptions. Loops forever; power-cycle to stop.

        .org    $2000

CSV     = $2080
SPCRV   = $2081
NLEN    = $2082
NLEN2   = $2083
PTR     = $2084
BUF     = $2100

start:  sei
        lds     #$01FF
loop:   jsr     cop
        jsr     rxb             ; cs
        stab    CSV
        jsr     rxb             ; spcr
        stab    SPCRV
        jsr     rxb             ; nlen
        stab    NLEN
        stab    NLEN2
        ldx     #BUF
        stx     PTR
rxd:    jsr     rxb
        ldx     PTR
        stab    0,x
        inx
        stx     PTR
        ldaa    NLEN
        deca
        staa    NLEN
        bne     rxd
        ldaa    SPCRV           ; execute the phase
        staa    $1028
        ldaa    $107E
        oraa    CSV
        staa    $107E
        ldx     #BUF
        ldab    NLEN2
        jsr     $9102
        ldaa    $107E
        anda    #$9E            ; clear bits 0,5,6
        staa    $107E
        bra     loop

rxb:    jsr     cop
        ldaa    $1074
        anda    #$20
        beq     rxb
        ldab    $1077
        rts

cop:    ldaa    #$55
        staa    $103A
        ldaa    #$AA
        staa    $103A
        rts
