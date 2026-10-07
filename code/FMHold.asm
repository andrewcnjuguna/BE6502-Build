; ================================================================
; FMHold - FMTest's tone, held until reset, for the scope
;
; Plays the same note and then rewrites the key-on register in a loop
; forever, so the FM select (A8/IO pad) and A5/A6 pad show a steady
; train of accesses to probe. Built from FMTest.asm; see there.
; ================================================================
;
; Original header:
; ================================================================
; OPL2 smoke test - one FM tone from the SIDKick pico's YM3812
; Ben Eater 6502 + GAL rev 02
;
; The SKpico emulates an OPL2 as its "SID #2" when that is set to FM
; at the IO address. GAL rev 02 drives the SKpico's A8/IO pad low for
; $5400-$57FF; with its A5 pad high and A0-A3 low, A4 picks the port:
;
;   $5420  OPL2 register address
;   $5430  OPL2 data
;
; Set it up first with SKConfig, RAM only:  2 then 4 (SID #2 = FM),
; 3 then 5 (IO address). A power cycle undoes it.
;
; Plays the AdLib programming guide's test note - one two-operator
; voice on channel 0, about 440 Hz - for two seconds, keys it off and
; returns to WozMon. If nothing sounds, check the config first, then
; GAL pin 16 to the A8/IO pad.
;
; Assemble:
;   vasm6502_oldstyle -Fbin -dotdir -wdc02 -o FMTest.bin FMTest.asm
; Load and run from WozMon:  400L  then  400R
; ================================================================

OPLADDR   = $5420
OPLDATA   = $5430

 .org $400

Start
    LDX #$01                ; silence: zero every register first
Clear
    LDA #$00
    JSR OPLWrite
    INX
    CPX #$F6
    BNE Clear

    LDY #0                  ; then the test note, pairs from the table
Note
    LDX Tone,Y
    BEQ Hold
    LDA Tone+1,Y
    JSR OPLWrite
    INY
    INY
    BNE Note

Hold
    LDX #$B0                ; keep the note keyed on, forever
    LDA #$31
    JSR OPLWrite
    JMP Hold                ; press reset to stop

; Register X <- A. The real chip wants 3.3 us after the address and
; 23 us after the data; the emulation is not fussy, but keep to it.
OPLWrite
    STX OPLADDR
    NOP
    NOP
    STA OPLDATA
    PHY
    LDY #8                  ; 8 x 5 cycles = 40 us
OPLW1
    DEY
    BNE OPLW1
    PLY
    RTS

DelayX                      ; X x ~330 ms
DX1
    LDY #$00
DX2
    PHX
    LDX #$00
DX3
    DEX
    BNE DX3
    PLX
    DEY
    BNE DX2
    DEX
    BNE DX1
    RTS

; register, value - ends at a register of 0
Tone
    .byte $20, $01          ; modulator: multiplier 1
    .byte $40, $10          ; modulator: output level
    .byte $60, $F0          ; modulator: fast attack, slow decay
    .byte $80, $77          ; modulator: sustain, release
    .byte $A0, $98          ; channel 0: F-number low
    .byte $23, $01          ; carrier: multiplier 1
    .byte $43, $00          ; carrier: full volume
    .byte $63, $F0          ; carrier: fast attack, slow decay
    .byte $83, $77          ; carrier: sustain, release
    .byte $B0, $31          ; channel 0: key on, block 4, F-number high
    .byte $00
