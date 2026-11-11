; ramdump.S — GM600 full-flash dumper, a 68HC11 stub that runs from RAM.
;
; Uploaded into RAM with SBEP `17` writes and launched with the `26` backdoor (sei + jmp),
; this streams the whole 256 KB external flash (U0102) out the SCI as one linear image:
; the 14 banked pages 0..13 through the window at $4000, then the fixed $8000..$FFFF
; (pages 14,15). It never returns — power-cycle the radio afterwards.
;
; Why a RAM stub: the SBEP `11` read only sees whichever bank is mapped at $4000, and a
; bank-walk can't work because the `11`/`17` handlers themselves live in that banked window
; (bank 04) — switching banks pulls the running code out from under the CPU. See ../FIRMWARE.md.
;
; Assembled by asm6811.py. Verified in-circuit on 807IYA0903: output byte-identical to the
; off-board T48 read (motofix.BIN), all 16 pages.

; Why $2000: it sits in the K-series internal RAM, clear of the firmware RAM the dump reads back
; through the window (the LCD/LED shadows at $34xx/$37xx and the SBEP buffers at $1Axx are all
; lower). ramdump.py proves it is writable RAM by reading the stub back and comparing before it
; launches; the stub then runs with interrupts off and never returns, so it only has to not overlap
; itself — whatever firmware data lived at $2000 is irrelevant once we have jumped in.
        .org    $3D80           ; free RAM: 0x3D80-0x3FFF reads zero in every RAM dump and nothing in the
;                                 firmware references it. NOT $2000 - that is the firmware's live
;                                 message-buffer pool, which can overwrite a stub between the host's
;                                 verify and the 0x26 launch (hence also the self-check below).

; ---- 68HC11 K-series register HAL (pinned from the firmware, see ../FIRMWARE.md) ----
BANKREG = $1058         ; bank select — value = page << 2
SCSR    = $1074         ; SCI status 1 — TDRE (transmitter empty) = bit 7
SCDR    = $1077         ; SCI data — a write transmits one byte
COPRST  = $103A         ; COP watchdog reset — kick = write $55 then $AA
TDRE    = $80           ; TDRE mask within SCSR

WINLO   = $4000         ; banked flash window, low
WINHI   = $8000         ; window end / start of the fixed region (pages 14,15)
LASTREG = $38           ; loop end: last banked page is 13 (reg $34); $34 + 4 = $38

; ---------------------------------- the dumper ----------------------------------
start:  sei                     ; no interrupts: we own the CPU until power-off
        ldx     #start          ; self-check: the image (incl. its .cksum byte) must sum to 0 -
        clra                    ; if the firmware overwrote any of it after the host verified it,
selfck: ldab    0,x             ; don't run it
        aba
        inx
        cpx     #codeend
        bne     selfck
        tsta
        beq     selfok
damaged: bra    damaged         ; no COP kick -> watchdog reset -> the radio reboots (safe)
selfok:
        ldab    #0              ; B = bank-register value for the current page (page << 2)

bank:   stab    BANKREG         ; map this page at $4000
        ldx     #WINLO
win:    ldaa    #$55            ; -- kick the COP watchdog so the ~4.5 min dump isn't reset --
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
wtx1:   ldaa    SCSR            ; -- wait for the SCI transmitter to be empty --
        anda    #TDRE
        beq     wtx1
        ldaa    0,x             ; read a flash byte through the banked window
        staa    SCDR            ; transmit it
        inx
        cpx     #WINHI
        bne     win             ; ...to the end of this 16 KB page
        addb    #4              ; advance to the next page (reg += 4)
        cmpb    #LASTREG
        bne     bank            ; ...for pages 0..13

        ldx     #WINHI          ; -- fixed region $8000..$FFFF (pages 14,15) --
fix:    ldaa    #$55
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
wtx2:   ldaa    SCSR
        anda    #TDRE
        beq     wtx2
        ldaa    0,x
        staa    SCDR
        inx
        bne     fix             ; X wraps $FFFF -> $0000 -> done
done:   bra     done            ; halt; the host power-cycles to recover

        .cksum                  ; balances the image to 0 (mod 256) for the self-check above
codeend:
