; lcd_text.S — GM600 RAM stub: show host-supplied segment patterns on the 3-digit 7-seg display.
;
; Uploaded with SBEP 0x17 and launched with 0x26 (like led_cmd.S). The three main digits are NOT in
; the 0x37AC LCD shadow: they go to a separate controller through SPI-dispatcher entry 5 (0x9018,
; chip-select 0x61, SPCR 0x52), which packs a 5-byte struct [b0 b1 d0 d1 d2] (d = one 7-seg byte
; per digit, same font as the cache at 0x3497) and caches the struct at 0x3495. The firmware calls it
; as: push struct ptr, push a word, ldd #5, jsr 0x8F1F, pull both — bracketed by the RTOS device lock
; 0x9FFB/0xA01A on 0x0245, which this stub skips (it is a pure semaphore, and with interrupts off a
; contended lock would block in the scheduler).
;
; Command loop: read a 0xA5 sync byte, then 5 bytes from the SCI (b0 b1 d0 d1 d2; the firmware's idle value is
; 00 40 xx xx xx), push them. The display holds them until the next command. Power-cycle to stop.

        .org    $2000

COPRST  = $103A
SCSR    = $1074         ; SCI status 1 (RDRF = bit5 = 0x20)
SCDR    = $1077         ; SCI data
PUSH    = $8F1F         ; SPI device dispatcher; B=5 -> 7-seg digit controller
BUF     = $2100         ; 5-byte struct

start:  sei
        lds     #$21FF
loop:   jsr     rxb             ; sync: wait for 0xA5 (skips stale/echo bytes)
        cmpb    #$A5
        bne     loop
        jsr     rxb
        stab    $2100
        jsr     rxb
        stab    $2101
        jsr     rxb
        stab    $2102
        jsr     rxb
        stab    $2103
        jsr     rxb
        stab    $2104
        ldx     #BUF
        pshx                    ; struct pointer (callee reads it at $C,y)
        pshx                    ; unused word
        ldab    #5              ; (dispatcher clears A)
        jsr     PUSH
        pulx
        pulx
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
