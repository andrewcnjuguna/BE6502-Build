; ================================================================
; HD44780 LCD driver, 4-bit mode, through a 74LS574 latch at $4C00
; Ben Eater 6502 + GAL decoder
;
; The 6522 cannot take the LCD any more - the Fast SD interface owns
; all of Port A (and reading it pulses CA2, clocking the SD card) and
; leaves only four bits of Port B. So the LCD gets its own address.
;
; Latch bit assignment (74LS574 Q outputs -> LCD):
;   Q0-Q3  ->  LCD D4-D7
;   Q4     ->  LCD RS
;   Q5     ->  LCD E
;   Q6-Q7  ->  spare
;   /OE tied to GND, CP from the GAL's LCDCS qualified with the write
;   LCD RW tied to GND - we never read, so every wait is a fixed delay
;
; LS574 outputs only guarantee 2.4V high and the HD44780 wants 3.5V,
; so fit 3.3k-10k pull-ups to +5V on the six outputs in use.
;
; Assemble:
;   vasm6502_oldstyle -Fbin -dotdir -wdc02 -o LCDTest.bin LCDTest.asm
; Load and run from WozMon:  400L  then  400R
;
; Timing assumes a 1 MHz clock. At 5 MHz every delay is 5x too short.
; ================================================================

LCD       = $4C00           ; anywhere in $4C00-$4FFF hits the latch
LCD_RS    = $10             ; Q4
LCD_E     = $20             ; Q5

; zero page - change these if they clash with whatever else is running
LCDPTR    = $30             ; and $31, string pointer
LCDRS     = $32             ; RS bit for the byte being sent
LCDTMP    = $33

 .org $400

Start
    SEI
    JSR LCDInit
    JSR LCDLine1
    LDA #<Msg1
    STA LCDPTR
    LDA #>Msg1
    STA LCDPTR+1
    JSR LCDPrint
    JSR LCDLine2
    LDA #<Msg2
    STA LCDPTR
    LDA #>Msg2
    STA LCDPTR+1
    JSR LCDPrint
    RTS                     ; back to WozMon, display holds

; ---------------------------------------------------------------
; Send the low nibble of A, with RS taken from LCDRS.
; Three writes: data with E low, E high, E low again. The HD44780
; latches on the FALLING edge of E, by which point the data has been
; stable for a full bus cycle.
; ---------------------------------------------------------------
LCDNib
    AND #$0F
    ORA LCDRS
    STA LCDTMP
    STA LCD                 ; data valid, E low
    ORA #LCD_E
    STA LCD                 ; E high
    LDA LCDTMP
    STA LCD                 ; E low -> latched
    RTS

; Send the whole byte in A as two nibbles, high first. Preserves Y.
LCDByte
    PHY
    PHA
    LSR
    LSR
    LSR
    LSR
    JSR LCDNib              ; high nibble
    PLA
    JSR LCDNib              ; low nibble
    JSR Delay40us
    PLY
    RTS

LCDCmd                      ; A = command byte
    PHA
    LDA #$00
    STA LCDRS
    PLA
    JMP LCDByte

LCDChar                     ; A = character
    PHA
    LDA #LCD_RS
    STA LCDRS
    PLA
    JMP LCDByte

LCDLine1
    LDA #$80                ; set DDRAM address $00
    JMP LCDCmd

LCDLine2
    LDA #$C0                ; set DDRAM address $40
    JMP LCDCmd

LCDClear
    LDA #$01
    JSR LCDCmd
    LDX #4                  ; clear needs 1.5ms
    JMP DelayMS

; Print the zero-terminated string at (LCDPTR)
LCDPrint
    LDY #$00
LCDPr1
    LDA (LCDPTR),Y
    BEQ LCDPr2
    JSR LCDChar
    INY
    BNE LCDPr1
LCDPr2
    RTS

; ---------------------------------------------------------------
; Power-on init. The first three nibbles are sent blind while the
; controller is still in 8-bit mode and can only see D4-D7.
; ---------------------------------------------------------------
LCDInit
    LDX #40                 ; >15ms after power-up
    JSR DelayMS
    LDA #$00
    STA LCDRS               ; everything here is a command

    LDA #$03
    JSR LCDNib
    LDX #4                  ; >4.1ms
    JSR DelayMS
    LDA #$03
    JSR LCDNib
    JSR Delay200us
    LDA #$03
    JSR LCDNib
    JSR Delay200us

    LDA #$02                ; switch to 4-bit
    JSR LCDNib
    JSR Delay200us

    LDA #$28                ; 4-bit, 2 lines, 5x8 font
    JSR LCDCmd
    LDA #$08                ; display off
    JSR LCDCmd
    JSR LCDClear
    LDA #$06                ; entry mode: increment, no shift
    JSR LCDCmd
    LDA #$0C                ; display on, cursor off, no blink
    JMP LCDCmd

; ---------------------------------------------------------------
; Delays, 1 MHz. Inner loop is 5 cycles.
; ---------------------------------------------------------------
Delay40us
    LDY #7
D40
    DEY
    BNE D40
    RTS

Delay200us
    LDY #38
D200
    DEY
    BNE D200
    RTS

DelayMS                     ; X x ~1.28ms
DMS1
    LDY #$00
DMS2
    DEY
    BNE DMS2
    DEX
    BNE DMS1
    RTS

Msg1 .byte "BE6502 SID",0
Msg2 .byte "LCD at $4C00",0
