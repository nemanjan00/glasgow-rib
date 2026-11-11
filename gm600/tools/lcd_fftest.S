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
        ldaa    #$57
        staa    $1028
        ldaa    #$41
        staa    $107E
        ldx     #$2080
        ldab    #3
        jsr     $9102
        clra
        staa    $107E
        ldaa    #$50
        staa    $1028
        ldaa    #$01
        staa    $107E
        ldx     #$37AC
        ldab    #21
        jsr     $9102
        clra
        staa    $107E
        bra     loop
cop:    ldaa    #$55
        staa    $103A
        ldaa    #$AA
        staa    $103A
        rts
