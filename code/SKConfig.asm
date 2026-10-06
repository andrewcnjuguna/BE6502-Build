; ================================================================
; SIDKick pico configuration over serial - Ben Eater 6502
; Port of skconfig.c from the Fast6502 build.
;
; On a C64 the SKpico is configured from a menu launched with
; SYS 54301. Underneath, that menu is a small register protocol on
; the SID's unused registers $1D-$1F (frntc/SIDKick-pico,
; Source/SKpico.c), which here are $481D-$481F:
;
;   write $1F <- $FF    enter config mode; it lapses after 25000
;                       PHI2 cycles without a config access (25 ms)
;   write $1E <- $00    point at config byte 0
;   write $1E <- $E0+n  point at character n of the version string
;   read  $1D           the byte pointed at; config bytes advance the
;                       pointer, version characters do not
;   write $1D <- n      store the next config byte, pointer advances
;   write $1D <- $FE    apply the configuration, RAM only
;   write $1D <- $FF    apply it and save it to flash
;
; $FA and $FB written to $1D are commands too, so a config byte of
; $FA or above cannot be stored. Store writes bytes 0-59, through the
; clock, and refuses if any of them is $FA or above. Bytes 52 and 53
; are named checksums in the firmware but nothing checks them; they
; go back as they were read. In config mode a write to any other
; register ends it, so it is entered once per exchange, and each
; exchange is one burst with nothing printed in the middle.
;
; Menu - each change is applied in RAM only:
;   1      SID #1 type     0 6581, 1 8580, 2 8580 digiboost
;   2      SID #2 type     0-2 as above, 3 none, 4 FM (OPL2, with
;                          the fake status AdLib detection wants), 5 FM
;   3      SID #2 address  0 $D400, 1 $D420 (A5), 2/3 $D500/$D520 (A8 -
;                          not here: the A8/IO pad carries FM select),
;                          4/5 IO pad - for FM use 5: OPL2 at $5420/$5430
;   4      clock           0 PAL, 1 NTSC, 2 old NTSC
;   d      dump again
;   s      save what is live now to flash (asks for y)
;   q      quit to WozMon
;
; Assemble:
;   vasm6502_oldstyle -Fbin -dotdir -wdc02 -o SKConfig.bin SKConfig.asm
; Load and run from WozMon:  400L  then  400R
;
; Needs the 1 MHz clock, like everything else that talks to the
; SKpico. Serial output assumes WozMon has already set up the ACIA.
; ================================================================

SID       = $4800
SKDATA    = SID+$1D
SKPTR     = SID+$1E
SKMODE    = SID+$1F

ACIA_DATA = $5000
ACIA_STAT = $5001
RDRF      = $08             ; receiver full
TDRE      = $10             ; transmitter empty - always set on a WDC part

; The W65C51N's TDRE bit is stuck high, so every character is also
; followed by a fixed wait. 220 x 5 cycles = 1.1 ms at 1 MHz, one
; character at 9600 baud; anything faster just prints a bit slowly.
TXDLY     = 220

CFG_SID1_TYPE    = 0
CFG_SID2_TYPE    = 8
CFG_SID2_ADDRESS = 10
CFG_CLOCKSPEED   = 59
STORE_BYTES      = 60       ; bytes 0-59, through CFG_CLOCKSPEED

; zero page - clear of WozMon ($20-$2F) and the SD code ($A2+)
STRP      = $30             ; and $31, string pointer
TMP       = $32
TMP2      = $33
FIELD     = $34             ; menu field being changed, 0-3

 .org $400

Start
    PHP                     ; put the I flag back on the way out
    SEI                     ; nothing else touches the ACIA meanwhile
    JSR Drain
    LDA #<MsgBanner
    LDX #>MsgBanner
    JSR Puts
    JSR Dump

Menu
    LDA #<MsgPrompt
    LDX #>MsgPrompt
    JSR Puts
    JSR GetKey
    PHA
    JSR PutC                ; echo it
    JSR CrLf
    PLA

    CMP #'1'                ; digits first - the case fold below
    BCC MenuLetter          ; would mangle them
    CMP #'5'
    BCS MenuLetter
    SEC
    SBC #'1'
    JMP SetField

MenuLetter
    AND #$DF                ; fold to upper case
    CMP #'Q'
    BEQ Quit
    CMP #'D'
    BNE Menu1
    JSR Dump
    JMP Menu
Menu1
    CMP #'S'
    BNE Menu
    JMP Save

Quit
    LDA #<MsgDone
    LDX #>MsgDone
    JSR Puts
    PLP
    RTS                     ; back to WozMon, as LCDTest does

; ---------------------------------------------------------------
; Menu actions. Both refuse unless the last read looked sane.
; ---------------------------------------------------------------
SetField                    ; A = field 0-3, see the Field tables
    STA FIELD
    TAX
    LDA AskHi,X
    PHA
    LDA AskLo,X
    PLX
    JSR Puts
    JSR GetKey
    PHA
    JSR PutC                ; echo it
    JSR CrLf
    PLA
    LDX FIELD
    CMP #'0'
    BCC SetBad
    CMP FieldTop,X
    BCS SetBad
    SEC
    SBC #'0'
    PHA
    JSR CanStore
    PLA
    BCC SetDone
    LDX FIELD
    LDY FieldIdx,X
    STA Config,Y
    LDA #$FE                ; apply, RAM only
    JSR Store
    LDA #<MsgApplied
    LDX #>MsgApplied
    JSR Puts
    JMP ReadBack
SetBad
    LDA #<MsgUnchanged
    LDX #>MsgUnchanged
    JSR Puts
SetDone
    JMP Menu

Save
    JSR CanStore
    BCC SaveDone
    LDA #<MsgConfirm
    LDX #>MsgConfirm
    JSR Puts
    JSR GetKey
    PHA
    JSR CrLf
    PLA
    CMP #'y'
    BEQ SaveYes
    CMP #'Y'
    BEQ SaveYes
    LDA #<MsgNotSaved
    LDX #>MsgNotSaved
    JSR Puts
SaveDone
    JMP Menu
SaveYes
    LDA #$FF                ; apply and save to flash
    JSR Store
    LDA #<MsgSaved
    LDX #>MsgSaved
    JSR Puts
ReadBack                    ; a flash save stalls the chip for a moment
    LDA #<MsgAnyKey
    LDX #>MsgAnyKey
    JSR Puts
    JSR GetKey
    JSR Dump
    JMP Menu

; Carry set if bytes 0-59 can go back to the chip. Prints why not.
CanStore
    JSR Silent
    BCC CanStore1
    LDA #<MsgNoAnswer
    LDX #>MsgNoAnswer
    JSR Puts
    CLC
    RTS
CanStore1
    LDX #0
CanStore2
    LDA Config,X
    CMP #$FA
    BCS CanStore3
    INX
    CPX #STORE_BYTES
    BNE CanStore2
    SEC
    RTS
CanStore3
    LDA #<MsgCommand
    LDX #>MsgCommand
    JSR Puts
    CLC
    RTS

; ---------------------------------------------------------------
; Talking to the chip. Each is one burst: no printing inside.
; ---------------------------------------------------------------
Fetch
    LDA #$FF
    STA SKMODE              ; $1F: config mode
    LDX #0
Fetch1
    TXA
    ORA #$E0                ; $1E: version character X
    STA SKPTR
    LDA SKDATA
    STA Version,X
    INX
    CPX #32
    BNE Fetch1
    LDA #$00                ; $1E: config byte 0
    STA SKPTR
    LDX #0
Fetch2
    LDA SKDATA              ; pointer advances on each read
    STA Config,X
    INX
    CPX #64
    BNE Fetch2
    RTS

Store                       ; A = $FE apply, $FF apply and save
    PHA
    LDA #$FF
    STA SKMODE
    LDA #$00
    STA SKPTR
    LDX #0
Store1
    LDA Config,X
    STA SKDATA
    INX
    CPX #STORE_BYTES
    BNE Store1
    PLA
    STA SKDATA
    RTS

; A real configuration is never 64 copies of one byte, but an
; unanswered read is: $FF from the pulled-up bus, or $FE with the
; SKpico's pull on D0. Carry set if silent.
Silent
    LDA Config
    LDX #1
Silent1
    CMP Config,X
    BNE Silent2
    INX
    CPX #64
    BNE Silent1
    SEC
    RTS
Silent2
    CLC
    RTS

; ---------------------------------------------------------------
; Dump: fetch, then print the version, the 64 bytes and the fields.
; ---------------------------------------------------------------
Dump
    JSR Fetch

    LDA #<MsgFirmware
    LDX #>MsgFirmware
    JSR Puts
    LDX #0
Dump1
    LDA Version,X
    BEQ Dump2
    JSR PutPrintable
    INX
    CPX #32
    BNE Dump1
Dump2
    JSR CrLf
    JSR CrLf

    LDX #0
Dump3
    TXA
    AND #$0F
    BNE Dump4
    TXA                     ; start of a row: "00: "
    JSR PutHex
    LDA #':'
    JSR PutC
    LDA #' '
    JSR PutC
Dump4
    LDA Config,X
    JSR PutHex
    LDA #' '
    JSR PutC
    TXA
    AND #$0F
    CMP #$0F
    BNE Dump5
    JSR CrLf                ; end of a row
Dump5
    INX
    CPX #64
    BNE Dump3
    JSR CrLf

    LDA #<FldSid1
    LDX #>FldSid1
    LDY #CFG_SID1_TYPE
    JSR Field
    LDA #<FldSid2
    LDX #>FldSid2
    LDY #CFG_SID2_TYPE
    JSR Field
    LDA #<FldAddr
    LDX #>FldAddr
    LDY #CFG_SID2_ADDRESS
    JSR Field
    LDA #<FldClock
    LDX #>FldClock
    LDY #CFG_CLOCKSPEED
    JSR Field

    JSR Silent
    BCC Dump6
    LDA #<MsgSilent
    LDX #>MsgSilent
    JSR Puts
Dump6
    RTS

; A/X = label, which carries its own legend line after the value.
; The label is "name|legend": printed up to '|', then the value,
; then the rest. Y = config index.
Field
    STA STRP
    STX STRP+1
    STY TMP2
    LDY #0
Field1
    LDA (STRP),Y
    CMP #'|'
    BEQ Field2
    JSR PutC
    INY
    BNE Field1
Field2
    INY
    STY TMP                 ; PutDec uses X and Y
    LDX TMP2
    LDA Config,X
    JSR PutDec
    JSR CrLf
    LDY TMP
Field3
    LDA (STRP),Y
    BEQ Field4
    JSR PutC
    INY
    BNE Field3
Field4
    RTS

; ---------------------------------------------------------------
; Serial
; ---------------------------------------------------------------
PutC                        ; preserves A, X, Y
    PHA
PutC1
    LDA ACIA_STAT
    AND #TDRE
    BEQ PutC1
    PLA
    STA ACIA_DATA
    PHX
    LDX #TXDLY
PutC2
    DEX
    BNE PutC2
    PLX
    RTS

CrLf
    LDA #13
    JSR PutC
    LDA #10
    JMP PutC

; The version string mixes ASCII with C64 screen codes: "SKpico" comes
; back as 53 4B 10 09 03 0F, lower case as screen codes 1-26.
PutPrintable
    CMP #1
    BCC PutDot
    CMP #27
    BCS PutPrint1
    ORA #$60                ; screen code 1-26 -> 'a'-'z'
    JMP PutC
PutPrint1
    CMP #32
    BCC PutDot
    CMP #127
    BCC PutC
PutDot
    LDA #'.'
    JMP PutC

PutHex
    PHA
    LSR
    LSR
    LSR
    LSR
    JSR PutNib
    PLA
    AND #$0F
PutNib
    PHX
    TAX
    LDA HexDigits,X
    PLX
    JMP PutC

PutDec                      ; A = 0-255, no leading zeros
    LDY #0                  ; Y = a digit has been printed
    LDX #0
PutDec1
    CMP #100
    BCC PutDec2
    SBC #100
    INX
    BNE PutDec1
PutDec2
    STA TMP2
    CPX #0
    BEQ PutDec3
    TXA
    ORA #'0'
    JSR PutC
    INY
PutDec3
    LDX #0
    LDA TMP2
PutDec4
    CMP #10
    BCC PutDec5
    SBC #10
    INX
    BNE PutDec4
PutDec5
    STA TMP2
    CPY #0
    BNE PutDec6             ; hundreds printed: tens always follow
    CPX #0
    BEQ PutDec7
PutDec6
    TXA
    ORA #'0'
    JSR PutC
PutDec7
    LDA TMP2
    ORA #'0'
    JMP PutC

Puts                        ; A/X = zero-terminated string, < 256 bytes
    STA STRP
    STX STRP+1
    LDY #0
Puts1
    LDA (STRP),Y
    BEQ Puts2
    JSR PutC
    INY
    BNE Puts1
Puts2
    RTS

GetKey
    LDA ACIA_STAT
    AND #RDRF
    BEQ GetKey
    LDA ACIA_DATA
    RTS

; Empty the receiver, so a stray byte from the load is not taken as
; a menu key. Gives up after 64 bytes in case RDRF sticks, which
; hung the unbounded version on the Fast6502.
Drain
    LDA #64
    STA TMP2
Drain1
    LDY #4                  ; 4 x 256 x 13 cycles = ~13 ms of quiet
    LDX #0
Drain2
    LDA ACIA_STAT
    AND #RDRF
    BNE Drain3
    DEX
    BNE Drain2
    DEY
    BNE Drain2
    RTS
Drain3
    LDA ACIA_DATA
    DEC TMP2
    BNE Drain1
    RTS

; ---------------------------------------------------------------
; Text
; ---------------------------------------------------------------
HexDigits   .byte "0123456789ABCDEF"

; menu fields 1-4: config byte, first digit not allowed, question
FieldIdx    .byte CFG_SID1_TYPE, CFG_SID2_TYPE, CFG_SID2_ADDRESS, CFG_CLOCKSPEED
FieldTop    .byte '3', '6', '6', '3'
AskLo       .byte <Ask1, <Ask2, <Ask3, <Ask4
AskHi       .byte >Ask1, >Ask2, >Ask3, >Ask4
Ask1        .byte "SID #1 type - 0 6581, 1 8580, 2 8580 digiboost: ",0
Ask2        .byte "SID #2 type - 0 6581, 1 8580, 2 8580 digiboost, 3 none, 4/5 FM: ",0
Ask3        .byte "SID #2 address - 0 $D400, 1 $D420 (A5), 4/5 IO pad (FM: 5): ",0
Ask4        .byte "clock - 0 PAL, 1 NTSC, 2 old NTSC: ",0
MsgUnchanged .byte "not changed",13,10,0

MsgBanner   .byte 13,10,"SIDKick pico configuration, via $481D-$481F",13,10,0
MsgPrompt   .byte 13,10,"1 SID #1 type, 2 SID #2 type, 3 SID #2 address, 4 clock (RAM only)",13,10
            .byte "d dump, s save to flash, q quit: ",0
MsgFirmware .byte 13,10,"firmware: ",0
MsgApplied  .byte "applied, not saved: a power cycle undoes it",13,10,0
MsgConfirm  .byte "Save what is live now to flash. Type y to confirm: ",0
MsgNotSaved .byte "not saved",13,10,0
MsgSaved    .byte "saved",13,10,0
MsgAnyKey   .byte "any key reads it back",13,10,0
MsgNoAnswer .byte "Refusing: nothing answered the config read.",13,10,0
MsgCommand  .byte "Refusing: a byte in 0-59 is $FA or above, a command on $1D.",13,10,0
MsgSilent   .byte 13,10,"Every byte the same: nothing is answering at $481D-$481F.",13,10
            .byte "Check the 1 MHz clock, A0-A4, and the SID /CS from the GAL.",13,10,0
MsgDone     .byte 13,10,"done, back to WozMon",13,10,0

FldSid1     .byte "SID #1 type    [0]  |"
            .byte "                     0 6581, 1 8580, 2 8580 digiboost",13,10,0
FldSid2     .byte "SID #2 type    [8]  |"
            .byte "                     0-2 as #1, 3 none, 4-5 FM",13,10,0
FldAddr     .byte "SID #2 address [10] |"
            .byte "                     0 $D400, 1 $D420 (A5 pad), 5 IO pad = FM at $5420",13,10,0
FldClock    .byte "clock          [59] |"
            .byte "                     0 PAL, 1 NTSC, 2 old NTSC: pitch comes from this",13,10,0

; ---------------------------------------------------------------
; Buffers - not part of the binary's contents that matter, but kept
; inside it so a load cannot leave them overlapping anything.
; ---------------------------------------------------------------
Config      .blk 64,0
Version     .blk 33,0       ; 32 reachable characters, and a 0
