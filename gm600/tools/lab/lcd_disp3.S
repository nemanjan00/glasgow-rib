; lcd_disp3.S — WORKING .S LCD driver: receive a 21-byte buffer over the SCI into 0x37AC and push it
; to the glass (firmware halted). Enable control head (0x107C), command 02 50 3F, data, with busy-wait
; delays replacing the firmware OC4 delays. Host sends 21 bytes per frame -> arbitrary content.
        .org    $2000
start:  sei
        lds     #$01FF
        ldaa    #$02
        staa    $2080
        ldaa    #$50
        staa    $2081
        ldaa    #$3F
        staa    $2082
loop:   jsr     cop
        ldx     #$37AC
        stx     $2084
        ldab    #21
        stab    $2086
rxl:    jsr     rxb
        ldx     $2084
        stab    0,x
        inx
        stx     $2084
        ldab    $2086
        decb
        stab    $2086
        bne     rxl
        clra
        staa    $1024
        ldaa    #$14
        staa    $107C
        jsr     dly
        ldaa    #$57
        staa    $1028
        ldaa    $107E
        oraa    #$41
        staa    $107E
        ldx     #$2080
        ldab    #3
        jsr     $9102
        ldaa    $107E
        anda    #$9E
        staa    $107E
        jsr     dly
        ldaa    #$50
        staa    $1028
        ldaa    $107E
        oraa    #$01
        staa    $107E
        ldx     #$37AC
        ldab    #21
        jsr     $9102
        ldaa    $107E
        anda    #$FE
        staa    $107E
        ldaa    #$10
        staa    $107C
        ldaa    #$40
        staa    $1024
        jsr     dly
        bra     loop
rxb:    jsr     cop
        ldaa    $1074
        anda    #$20
        beq     rxb
        ldab    $1077
        rts
dly:    ldx     #$4000
dl1:    dex
        bne     dl1
        rts
cop:    ldaa    #$55
        staa    $103A
        ldaa    #$AA
        staa    $103A
        rts
