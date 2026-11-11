        .org    $2000
start:  sei
        lds     #$01FF
        ldaa    #$02
        staa    $2100
        ldaa    #$50
        staa    $2101
        ldaa    #$3F
        staa    $2102
        ldx     #$37AC
        ldab    #21
        ldaa    #$FF
mk:     staa    0,x
        inx
        decb
        bne     mk
        jsr     push
        jsr     big
        jsr     big
        ldx     #$37AC
        stx     $2104
loop:   jsr     cop
        ldx     #$37AC
        ldab    #21
        clra
zb:     staa    0,x
        inx
        decb
        bne     zb
        ldx     $2104
        ldaa    #$FF
        staa    0,x
        jsr     push
        jsr     big
        ldx     $2104
        inx
        stx     $2104
        cpx     #$37C1
        bne     loop
        ldx     #$37AC
        stx     $2104
        bra     loop
push:   clra
        staa    $1024
        ldaa    #$14
        staa    $107C
        jsr     dly
        ldaa    #$57
        staa    $1028
        ldaa    $107E
        oraa    #$41
        staa    $107E
        ldx     #$2100
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
        rts
big:    ldab    #14
bl:     jsr     dly
        jsr     cop
        decb
        bne     bl
        rts
dly:    ldx     #$FFFF
dl1:    dex
        bne     dl1
        rts
cop:    ldaa    #$55
        staa    $103A
        ldaa    #$AA
        staa    $103A
        rts
