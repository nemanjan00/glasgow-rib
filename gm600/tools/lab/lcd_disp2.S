; lcd_disp2.S — sei-stub manual push with control-head enable (0x107C) + busy-wait delays replacing
; the firmware OC4-timed delays. cmd 02 50 3F @0x2080. CS via RMW (oraa/anda) on 0x107E.
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
        ldab    #21
        ldaa    #$FF
fill:   staa    0,x
        inx
        decb
        bne     fill
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
dly:    ldx     #$4000
dl1:    dex
        bne     dl1
        rts
cop:    ldaa    #$55
        staa    $103A
        ldaa    #$AA
        staa    $103A
        rts
