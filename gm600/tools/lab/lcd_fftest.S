; lcd_fftest.S — tight-loop: fill 0x37AC=all-FF, push to the glass replicating the firmware refresh
; EXACTLY, incl. CS on 0x107E via read-modify-write (oraa/anda) to preserve the other device-select
; bits (a full-byte store was the bug). Launch via 0x26; recover with the PSU (tools/psu_reboot.py).
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
        ldaa    #$57            ; --- command: SPCR=57, CS|=0x41, send 02 50 3F, CS&=~0x61 ---
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
        ldaa    #$50            ; --- data: SPCR=50, CS|=0x01, send 21 from 0x37AC, CS&=~0x01 ---
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
        bra     loop
cop:    ldaa    #$55
        staa    $103A
        ldaa    #$AA
        staa    $103A
        rts
