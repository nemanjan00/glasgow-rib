; pl_patch.S — FLASH patch (not a RAM stub): accessory pin 4 = "speaker unmuted", persistently.
;
; Pin 4 (EXTERNAL_ALARM) is ASFIC GCB1 = bit 1 of byte 8 of the ASFIC image at 0x37AC; bit 0 of the same
; byte is GCB0 = speaker-amp mute (1 = muted), which the firmware clears when the squelch opens (on a
; PL channel: when the tone is decoded). Every send of the image goes through SPI-dispatcher entry 8
; (CPU 0x8F7F, fixed bank: `ldab #21; ldx #$37AC; ...`). The patch replaces those two instructions
; (5 bytes) with `jsr plfix; nop; nop`. plfix sets GCB1 = NOT GCB0 in the image (interrupts off, so it
; can't interleave with the firmware's own updates of byte 8; the power-latch bit 2 is not touched),
; then does the two replaced instructions and returns. So the ASFIC always receives pin 4 = unmuted.
;
; Lives in erased flash in the fixed bank (CPU 0xDE00 = file 0x3DE00, page 15, below the 0xE000 boot
; block). See gm600py/firmware/pl_patch.py for the image patch / diff.

        .org    $DE00

GCBYTE  = $37B4         ; 0x37AC + 8

plfix:  tpa                     ; save the I bit
        psha
        sei
        ldab    GCBYTE
        orab    #$02            ; assume unmuted: pin 4 on
        bitb    #$01            ; GCB0 = muted?
        beq     store
        andb    #$FD            ; muted: pin 4 off
store:  stab    GCBYTE
        pula
        tap                     ; restore the I bit
        ldab    #$15            ; the two instructions the jsr replaced
        ldx     #$37AC
        rts
