; led_sweep.S — GM600 RAM stub: sweep the front-panel LEDs (a visible demo).
;
; Uploaded to RAM with SBEP 0x17 and launched with the 0x26 backdoor (sei + jmp), like ramdump.S /
; flashtool.S. The firmware is halted; this stub owns the CPU. It walks a single bit up and down
; through the 8-bit LED shadow and calls the firmware's own LED-push routine each step, so the LEDs
; chase. Runs forever -- power-cycle the radio to stop.
;
; The LEDs are driven from RAM shadow 0x34AE, pushed to the front-panel shift register by the SPI
; device dispatcher at 0x8F1F (ldab #10 selects the LED entry 0x8F64). See ../FIRMWARE.md. SPI was
; set up by the firmware during boot (SPCR=0x50), so the push works from this halted-firmware stub.

        .org    $2000

COPRST  = $103A         ; COP watchdog kick (write 0x55 then 0xAA)
LEDSH   = $34AE         ; LED shadow byte (pushed to the shift register)
PUSH    = $8F1F         ; SPI device dispatcher; B=10 -> push LEDs (entry 0x8F64)
LEDVAL  = $2090         ; RAM scratch: current sweep value (survives calls that clobber A)

start:  sei
        lds     #$01FF

restart: ldaa   #$01            ; start with the lowest LED
up:     staa    LEDVAL
        jsr     setled
        jsr     delay
        ldaa    LEDVAL
        asla                    ; walk the bit up (01,02,...,80)
        bne     up              ; 80<<1 = 00 -> Z set -> fall through
        ldaa    #$80            ; now walk back down
down:   staa    LEDVAL
        jsr     setled
        jsr     delay
        ldaa    LEDVAL
        lsra                    ; (80,40,...,01)
        bne     down            ; 01>>1 = 00 -> Z set -> fall through
        bra     restart

; --- set the LED shadow to LEDVAL and push it to the shift register ---
setled: ldaa    LEDVAL
        staa    LEDSH
        ldab    #10             ; LED entry in the SPI dispatcher table
        jsr     PUSH
        rts

; --- COP kick ---
cop:    ldaa    #$55
        staa    COPRST
        ldaa    #$AA
        staa    COPRST
        rts

; --- ~180 ms delay (E ~= 3.69 MHz), kicking the COP so the watchdog never fires ---
delay:  ldab    #2              ; outer count (cop preserves B; clobbers only A)
dlo:    jsr     cop
        ldx     #$FFFF
dli:    dex
        bne     dli
        decb
        bne     dlo
        rts
