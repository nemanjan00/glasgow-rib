; lcd_arb.S — drive the GM600 LCD with an arbitrary 21-byte buffer, replicating the firmware
; refresh protocol byte-for-byte (traced in emu6811):
;   command phase: SPCR=0x57, CS(0x107E)=0x41, send [02 50 3F] via firmware SPI primitive 0x9102,
;                  then CS=0x00
;   data phase:    SPCR=0x50, CS=0x01, send the 21-byte 0x37AC buffer via 0x9102, then CS=0x00
; Loop: receive 21 bytes over the SCI into 0x37AC, push. Launch via 0x26; power-cycle / 0x10 to stop.
        .org    $2000
COPRST  = $103A
SCSR    = $1074
SCDR    = $1077
SPCR    = $1028
CSPORT  = $107E
SPISND  = $9102         ; firmware SPI block send: X=buffer, B=count
LCDBUF  = $37AC
CMDBUF  = $2080         ; 3-byte command 02 50 3F
PTR     = $2084
CNT     = $2086

start:  sei
        lds     #$01FF
        ldaa    #$02
        staa    CMDBUF
        ldaa    #$50
        staa    $2081
        ldaa    #$3F
        staa    $2082
loop:   jsr     cop
        ldx     #LCDBUF         ; receive 21 bytes into 0x37AC
        stx     PTR
        ldab    #21
        stab    CNT
rxlp:   jsr     rxb
        ldx     PTR
        stab    0,x
        inx
        stx     PTR
        ldab    CNT
        decb
        stab    CNT
        bne     rxlp
        jsr     cop
        ldaa    #$57            ; --- command phase ---
        staa    SPCR
        ldaa    #$41
        staa    CSPORT
        ldx     #CMDBUF
        ldab    #3
        jsr     SPISND
        clra
        staa    CSPORT
        ldaa    #$50            ; --- data phase ---
        staa    SPCR
        ldaa    #$01
        staa    CSPORT
        ldx     #LCDBUF
        ldab    #21
        jsr     SPISND
        clra
        staa    CSPORT
        bra     loop

rxb:    jsr     cop
        ldaa    SCSR
        anda    #$20
        beq     rxb
        ldab    SCDR
        rts
cop:    ldaa    #$55
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts
