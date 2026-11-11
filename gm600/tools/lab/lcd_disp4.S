; lcd_disp4.S — sei-stub that pushes the 21-byte buffer PRELOADED at 0x2100 to the glass (firmware
; halted). Scratch cmd buffer at 0x2200 (outside code). Proven push: 0x107C enable + busy-wait delays.
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
        ldx     #$2100
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
