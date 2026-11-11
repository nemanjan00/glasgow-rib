; lcd_segstub2.S — reliable sweep stub: own COP-kicking SPI send (mysend) instead of 0x9102, so a
; SPIF stall cannot COP-reset the radio (which was aliasing to the boot self-test). Fill 0x37AC=0,
; set one byte (staa $37AC operand patched per-N), push. cmd buf @0x2200.
        .org    $2000
start:  sei
        lds     #$01FF
        ldaa    #$02
        staa    $2200
        ldaa    #$50
        staa    $2201
        ldaa    #$3F
        staa    $2202
loop:   jsr     cop
        ldx     #$37AC
        ldab    #21
        clra
zb:     staa    0,x
        inx
        decb
        bne     zb
        ldaa    #$FF
        staa    $37AC
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
        ldx     #$2200
        ldab    #3
        jsr     mysend
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
        jsr     mysend
        ldaa    $107E
        anda    #$FE
        staa    $107E
        ldaa    #$10
        staa    $107C
        ldaa    #$40
        staa    $1024
        jsr     dly
        bra     loop
mysend: jsr     cop
        ldaa    0,x
        staa    $102A
msw:    jsr     cop
        ldaa    $1029
        anda    #$80
        beq     msw
        ldaa    $102A
        inx
        decb
        bne     mysend
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
