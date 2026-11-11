; flashtool.S — resident GM600 RAM stub: AT29C020 probe / read / program over the SCI.
;
; Like ramdump.S this is uploaded to RAM with SBEP `17` and launched with the `26` backdoor, but it
; does NOT stream-and-halt: it sits in a command loop (interrupts off) and serves one-byte commands
; from the host until power-off. See ../FLASH_WRITE.md for the AT29C020 command set and the
; device-address -> bank/window mapping used here, and the AT29C020 / 68HC11K4 datasheets under
; datasheets/.
;
;   'I' (0x49) PROBE : AT29C020 software product ID + boot-block lockout. Replies 4 bytes:
;                      manufacturer (expect 1F), device (expect DA), lower-block lockout
;                      (FE=programmable / FF=locked), upper-block lockout. No data writes -> SAFE.
;   'R' (0x52) READ  : host sends page, winhi, winlo (a 256-byte-aligned window addr 0x4000-0x7F00);
;                      replies the 256 bytes of that sector.
;   'W' (0x57) WRITE : host sends page, winhi, winlo, then 256 data bytes; the stub issues the SDP
;                      program prefix, loads the sector, waits tWC, and replies status byte 0x84.
;
; The bus is single-wire half-duplex, so every byte the stub transmits echoes back onto its own RX;
; txb drains that echo. The host (flashtool.py) reads back each written sector to verify.
; Far control transfers use jsr/jmp (branches only reach +/-128); only tight local loops are branches.
; DANGER: `26` runs this with interrupts off; only ever launch the address you just uploaded.

        .org    $3D80           ; free RAM: 0x3D80-0x3FFF reads zero in every RAM dump and nothing in the
;                                 firmware references it. NOT $2000 - that is the firmware's live
;                                 message-buffer pool, which can overwrite a stub between the host's
;                                 verify and the 0x26 launch (hence also the self-check below).

SCSR    = $1074         ; SCI status 1 (RDRF = bit5 0x20, TDRE = bit7 0x80)
SCDR    = $1077         ; SCI data
BANK    = $1058         ; bank register = page << 2
COPRST  = $103A         ; COP kick = write 0x55 then 0xAA
PORTA   = $1000         ; PA6 (bit6) gates flash /OE (via U0106 AND gate) once pin-12 > ~10V
DDRA    = $1001         ; DDA6 = PA6 direction
TCTL1   = $1020         ; OM2:OL2 (bits 7:6) = OC2 action on PA6; clear to use PA6 as a plain output
WEMASK  = $40           ; PA6 bit

CNT16   = $2280         ; loop counter (16-bit, saved across X reuse)
WINPTR  = $2282         ; working window pointer (hi)
WINLO   = $2283         ; working window pointer (lo)
BUFPTR  = $2284         ; working buffer pointer
PAGE    = $2286         ; target page of a 'W' command (whitelist-checked)
BUF     = $2300         ; 256-byte sector buffer

; ------------------------------------------------------------------ command loop
start:  sei
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
        jsr     we_setup        ; free PA6 from OC2, make it an output, start in read mode (/OE low)
loop:   jsr     cop
        jsr     rxb             ; B = command byte
        cmpb    #$49            ; 'I'
        bne     n1
        jmp     do_id
n1:     cmpb    #$52            ; 'R'
        bne     n2
        jmp     do_read
n2:     cmpb    #$57            ; 'W'
        bne     loop
        jmp     do_write

; ------------------------------------------------------------------ helpers
cop:    ldaa    #$55            ; COP watchdog kick
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts

rxb:    jsr     cop             ; wait for a received byte -> B (host->radio, no echo)
        ldaa    SCSR
        anda    #$20            ; RDRF
        beq     rxb
        ldab    SCDR
        rts

txb:    ldaa    SCSR            ; send B, then swallow our own single-wire echo
        anda    #$80            ; TDRE
        beq     txb
        stab    SCDR
txe:    ldaa    SCSR
        anda    #$20            ; RDRF = the echo of what we just sent
        beq     txe
        ldaa    SCDR            ; discard it
        rts

; --- flash write-enable (PA6 -> flash /OE when pin-12 > ~10V). High = write mode, low = read. ---
we_setup: ldaa  TCTL1           ; detach OC2 from PA6 (OM2:OL2 = 0) so PA6 is a plain output
        anda    #$3F
        staa    TCTL1
        ldaa    DDRA            ; PA6 = output
        oraa    #WEMASK
        staa    DDRA
we_off: ldaa    PORTA           ; PA6 low -> flash /OE low -> read mode (flash drives data)
        anda    #$BF
        staa    PORTA
        rts
we_on:  ldaa    PORTA           ; PA6 high -> flash /OE high -> write mode (flash off the bus)
        oraa    #WEMASK
        staa    PORTA
        rts

; ------------------------------------------------------------------ AT29C020 product-ID commands
; Commands go through the fixed region $8000-$FFFF. /CE (=CSPROG pin) asserts for both the fixed
; region and the bank window (ramdump reads every page through the window, and the sector load below
; writes the window), so either would select the flash; the fixed region is just simpler (no banking)
; and unambiguously in range. The AT29C020 command decoder uses only A0-A14, so chip 0x5555 == CPU
; 0xD555 and 0x2AAA == CPU 0xAAAA (A15-A17 don't-care). The write lock was /OE, not chip-select.
id_cmd: ldaa    #$AA            ; entry: AA->5555, 55->2AAA, 90->5555  (via fixed region)
        staa    $D555
        ldaa    #$55
        staa    $AAAA
        ldaa    #$90
        staa    $D555
        rts

id_exit: ldaa   #$AA            ; exit: AA->5555, 55->2AAA, F0->5555
        staa    $D555
        ldaa    #$55
        staa    $AAAA
        ldaa    #$F0
        staa    $D555
        rts

; ------------------------------------------------------------------ 'I' probe (ID + lockout)
do_id:  jsr     we_on           ; PA6 high: enable flash writes for the command sequence
        jsr     id_cmd
        jsr     we_off          ; PA6 low: back to read mode to fetch the ID codes
        ldx     #2000           ; short settle after ID entry before reading
idwt:   jsr     cop
        dex
        bne     idwt
        ldab    $8000           ; manufacturer: A0=0 in ID mode (fixed region)
        jsr     txb
        ldab    $8001           ; device: A0=1
        jsr     txb
        clrb
        stab    BANK            ; page 0 for the window read of the lower lockout
        ldab    $4002           ; lower boot-block lockout (chip 0x0002) via window read
        jsr     txb
        ldab    $FFF2           ; upper boot-block lockout (chip 0x3FFF2) via fixed region
        jsr     txb
        jsr     we_on
        jsr     id_exit
        jsr     we_off
        jmp     loop

; ------------------------------------------------------------------ 'R' read sector -> host
do_read: jsr    rxb             ; page
        aslb                    ; MM1CR bank value = page << 2 (see ramdump.S); NOT the raw page
        aslb
        stab    BANK
        jsr     rxb             ; window hi
        stab    WINPTR
        jsr     rxb             ; window lo
        stab    WINLO
        ldx     #256
rdlp:   stx     CNT16
        ldx     WINPTR
        ldab    0,x
        inx
        stx     WINPTR
        jsr     txb
        ldx     CNT16
        dex
        bne     rdlp
        jmp     loop

; ------------------------------------------------------------------ 'W' program sector
do_write:
        jsr     rxb             ; page
        stab    PAGE
        jsr     rxb             ; window hi
        stab    WINPTR
        jsr     rxb             ; window lo
        stab    WINLO
        ldx     #BUF            ; receive 256 data bytes into BUF (consume the whole command first)
        stx     BUFPTR
        ldx     #256
wrxlp:  stx     CNT16
        jsr     rxb
        ldx     BUFPTR
        stab    0,x
        inx
        stx     BUFPTR
        ldx     CNT16
        dex
        bne     wrxlp
        ldab    PAGE            ; WHITELIST page: 0 (patch) or 12/13 (erased test pages). Checked
        cmpb    #0              ; BEFORE the <<2 encode. Page 3 is LIVE boot code — never writable.
        beq     wpg
        cmpb    #12
        beq     wpg
        cmpb    #13
        bne     wrej
wpg:    ldab    WINPTR          ; window hi MUST be 0x40-0x7F (the bank window) — never the fixed
        cmpb    #$40            ; region ($80+), or a bad header byte could program pages 14/15
        bcs     wrej            ; (vectors/reset) while PAGE still read 0.
        cmpb    #$80
        bcc     wrej
        ldab    WINLO           ; and sector-aligned (low byte 0)
        bne     wrej
        bra     wpok
wrej:   ldab    #$85            ; refuse: program nothing, reply fail status
        jsr     txb
        jmp     loop
wpok:   jsr     we_on           ; PA6 high: enable flash writes for the prefix + sector load
        ldaa    #$AA            ; SDP program prefix via CSPROG region (chip A0-A14, A15-17 n/c)
        staa    $D555
        ldaa    #$55
        staa    $AAAA
        ldaa    #$A0
        staa    $D555
        ldab    PAGE            ; target page
        aslb                    ; MM1CR bank value = page << 2 (see ramdump.S); NOT the raw page
        aslb
        stab    BANK
        ldx     #BUF            ; copy BUF -> window (the 256-byte sector load)
        stx     BUFPTR
        ldx     #256
wcplp:  stx     CNT16
        jsr     cop
        ldx     BUFPTR
        ldab    0,x
        inx
        stx     BUFPTR
        ldx     WINPTR
        stab    0,x
        inx
        stx     WINPTR
        ldx     CNT16
        dex
        bne     wcplp
        ldaa    #30             ; wait out tWC (>=15 ms), kicking the COP
wdo:    psha                    ; cop clobbers A, so preserve the outer counter across the kick
        jsr     cop
        pula
        ldx     #2000
wdi:    dex
        bne     wdi
        deca
        bne     wdo
        jsr     we_off          ; PA6 low: back to read mode
        ldab    #$84            ; status
        jsr     txb
        jmp     loop

        .cksum                  ; balances the image to 0 (mod 256) for the self-check above
codeend:
