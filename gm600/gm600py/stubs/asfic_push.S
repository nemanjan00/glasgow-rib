; asfic_push.S — GM600 NON-halting RAM stub: push the 21-byte ASFIC image at 0x37AC to the ASFIC now.
;
; The firmware sends 0x37AC (SPI dispatcher entry 8, decoder select code 2 = ASFIC PGMENAB) only when
; it changes something itself, and rebuilds the bytes it owns from its own state. To try a bit of our
; own (e.g. a general-control bit), write 0x37AC with 0x17, then launch this stub with 0x26: it takes
; the SPI bus the way the firmware's own display code does (0x5013 acquire / 0x5032 release, page 4),
; calls entry 8, and returns into the firmware through the 0x26 handler's epilogue (like keyevt.S).
; The ASFIC keeps the pushed value until the firmware's next push.

        .org    $3D80

BANK    = $1058
PAGE4   = $10           ; bank-register value for page 4 (page = value >> 2)
SPIACQ  = $5013         ; page 4: acquire the SPI bus (task-owned lock)
SPIREL  = $5032         ; page 4: release it
SPIDISP = $8F1F         ; fixed bank: SPI dispatcher, B = entry
EPILOG  = $4CE6         ; page 1: the 0x26 handler's own exit (pulx; rts)

start:  ldx     #start          ; self-check (Fletcher-style): a damaged copy spins instead of
        clra                    ; running inside the live firmware; the COP watchdog then resets
        clrb
selfck: adda    0,x
        psha
        aba
        tab
        pula
        inx
        cpx     #codeend
        bne     selfck
        tsta
        bne     damaged
        tstb
        beq     selfok
damaged: bra    damaged
selfok: cli                     ; a task normally runs with interrupts on
        ldaa    BANK            ; save the bank (page 1 while the SBEP handler runs)
        psha
        ldaa    #PAGE4
        staa    BANK
        jsr     SPIACQ
        ldab    #8              ; entry 8: 21 bytes from 0x37AC to the ASFIC
        jsr     SPIDISP
        jsr     SPIREL
        pula
        staa    BANK            ; back to the handler's page
        jmp     EPILOG          ; pulx; rts - return to the SBEP dispatcher

        .cksum16                ; zeroes both self-check sums
codeend:
