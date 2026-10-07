; SPDX-License-Identifier: GPL-2.0-or-later
; ================================================================
; Doom music on the BE6502 - Doom's OPL2 driver, in 6502 code
;
; Plays a MUS score through the SIDKick pico's OPL2 (GAL rev 02:
; register address $5420, data $5430). Built by doomplay.py, which
; appends the score and tables; assemble that, not this.
;
; The driver logic - voice allocation and stealing, instrument
; loading, the volume and frequency calculations, pitch bend - follows
; Chocolate Doom's src/i_oplmusic.c (C) 1993-1996 Id Software, Inc.,
; (C) 2005-2014 Simon Howard, as ported in doomopl.py: Doom v1.9 on
; an OPL2, music volume at maximum. GPL-2.0-or-later for that reason.
; doomplay.py --check runs this in py65 and compares every OPL2 write
; against doomopl.py.
;
; The score is packed (see doomplay.py): tokens 0nnnnnnn are a literal
; run of n+1 bytes; 1lllllll t t s replay MINREF+l bytes starting s
; bytes into the token at score offset t. GetByte follows those with
; a stack of frames and never needs the unpacked score.
;
; Timing: MUS ticks are 1/140 s; VIA T1 free-runs at 140 Hz and the
; flag is polled, as the SID driver does. Loops at the end.
;
; Running the driver on the tick was too slow at 1 MHz: a chord can
; mean 50-90 register writes, and busy ticks took up to three times
; the 7,140 cycles a tick has. So the driver runs ahead instead, one
; event at a time in the idle time between ticks, and queues its
; writes with tick markers; on each tick the queue for that tick goes
; straight out at ~25 cycles a write. The median tick is idle.
; ================================================================

OPLA      = $5420
OPLD      = $5430
VIA_ACR   = $600b
VIA_T1CL  = $6004
VIA_T1CH  = $6005
VIA_IFR   = $600d
VIA_T2CL  = $6008
VIA_T2CH  = $6009
TICKCYC   = 1000000 / 140      ; cycles per MUS tick
SK_DATA   = $481d
SK_PTR    = $481e
SK_MODE   = $481f
T1N       = 1000000 / 140 - 2      ; 140 Hz

; zero page
ZP        = $30
P         = ZP+0        ; general pointer
Q         = ZP+2        ; instrument voice data pointer
CH        = ZP+4        ; event channel
KEY       = ZP+5
VOLX      = ZP+6
VV        = ZP+7        ; voice index
II        = ZP+8        ; list index
INS       = ZP+9        ; instrument slot
IV        = ZP+10       ; instrument voice 0/1
NOTE      = ZP+11
GB_B      = ZP+12
TL        = ZP+13       ; 16-bit temps
TH        = ZP+14
UL        = ZP+15
UH        = ZP+16
WAITL     = ZP+17
WAITH     = ZP+18
EVB       = ZP+19
TMP       = ZP+20
TMP2      = ZP+21
FDEPTH    = ZP+22
MUL1      = ZP+23
MUL2      = ZP+24
CAR       = ZP+25
NK        = ZP+26       ; pitch-bend list counts
NM        = ZP+27
QHEAD     = ZP+28       ; write queue: next to send
QTAIL     = ZP+29       ;              next free
QWAIT     = ZP+30       ; ticks until the next queued writes are due
QSAVY     = ZP+31
QLATE     = ZP+32       ; ticks the queue ran dry; taken off later waits
CTL       = ZP+33       ; controller number - not TMP, which GetByte uses
CLKL      = ZP+34       ; Timer 2 as just read
CLKH      = ZP+35
LASTL     = ZP+36       ; Timer 2 at the previous read
LASTH     = ZP+37
ACCL      = ZP+38       ; cycles elapsed and not yet spent on ticks
ACCH      = ZP+39

; page 2 - WozMon's input buffer, free while this runs
FR_PL     = $0200       ; decoder frames, 16 deep
FR_PH     = $0210
FR_RL     = $0220
FR_RH     = $0230
FR_SL     = $0240       ; skip, low
FR_LT     = $0250
V_CH      = $0260       ; nine voices
V_KEY     = $0269
V_NOTE    = $0272
V_INS     = $027b
V_IV      = $0284
V_FL      = $028d
V_FH      = $0296
V_NVOL    = $029f
V_CAR     = $02a8
V_MOD     = $02b1
FREEL     = $02ba
ALLOCL    = $02c3
NFREE     = $02cc
NALLOC    = $02cd
C_INS     = $02d0       ; sixteen channels
C_VOL     = $02e0
C_BEND    = $02f0
; QREG/QVAL, 256 bytes each, follow the score: see doomplay.py
FR_SH     = $0190       ; skip, high - low in the stack page, far below the stack
C_VEL     = $01a0
KEPT      = $01b0
MOVED     = $01c0

DOOM_START
        sei
        cld
        ldx #$ff
        txs
        jsr SKFM               ; SID #2 = FM at the IO pad, RAM only
        jsr OPLInit
        ldx #16                ; ~20 ms: let the SKpico drain init
        jsr SKDelay
        jsr VoicesInit
        jsr ChannelsInit
        jsr ScoreInit
        lda #0
        sta QHEAD
        sta QTAIL
        sta QWAIT
        sta QLATE
-       jsr Room               ; fill the queue before the clock starts:
        bcc +                  ; the first tick loads every instrument
        jsr Step
        jmp -
; The clock is Timer 2, started once and left to count down at 1 MHz
; and wrap. A timer flag only remembers one timeout: E1M5's long volume
; fades made single steps run past a tick, a tick was lost each time,
; and by the end it was 60 ticks behind. Counting elapsed cycles loses
; nothing - a late tick is still a tick, run as soon as the loop is back.
+       lda #0                 ; T2 one-shot: it keeps counting past zero
        sta VIA_ACR
        sta ACCL
        sta ACCH
        lda #$ff
        sta LASTL
        sta LASTH
        sta VIA_T2CL
        sta VIA_T2CH           ; starts it at $ffff
Main    jsr Clock
-       lda ACCL               ; a tick's worth elapsed?
        sec
        sbc #<TICKCYC
        tax
        lda ACCH
        sbc #>TICKCYC
        bcc Produce
        sta ACCH
        stx ACCL
        jsr Consume
        jmp -

Clock   lda VIA_T2CH           ; read it so a carry between bytes is caught
        sta CLKH
        lda VIA_T2CL
        sta CLKL
        lda VIA_T2CH
        cmp CLKH
        beq +
        sta CLKH
        lda VIA_T2CL
        sta CLKL
+       sec                    ; elapsed = last - now; it counts down
        lda LASTL
        sbc CLKL
        tax
        lda LASTH
        sbc CLKH
        tay
        lda CLKL
        sta LASTL
        lda CLKH
        sta LASTH
        clc
        txa
        adc ACCL
        sta ACCL
        tya
        adc ACCH
        sta ACCH
        rts
Produce jsr Room
        bcc Main
        jsr Step
        jmp Main

Room    lda QHEAD              ; C set if one event's writes fit (at most ~33)
        clc
        sbc QTAIL              ; head - tail - 1 = free entries
        cmp #48
        rts

; One tick: send what is due, up to the next marker.
Consume lda QWAIT
        beq +
        dec QWAIT
        bne CRet
+       ldx QHEAD
CLoop   cpx QTAIL
        beq CUnder
        lda QREG,x
        beq CMark
        sta OPLA
        nop
        nop
        lda QVAL,x
        sta OPLD
        ldy #4                 ; ~40 us a write, see OPLW
-       dey
        bne -
        inx
        jmp CLoop
CMark   lda QVAL,x             ; wait d ticks, less any we are behind
        inx
        sec
        sbc QLATE
        beq CNow               ; caught up exactly: next group is due now
        bcc CBehind            ; still behind: next group is due now too
        sta QWAIT
        lda #0
        sta QLATE
        stx QHEAD
        rts
CNow    sta QLATE
        jmp CLoop
CBehind eor #$ff               ; late -= d
        clc
        adc #1
        sta QLATE
        jmp CLoop
CUnder  inc QLATE              ; the driver is behind this tick
        bne +
        dec QLATE
+       stx QHEAD
CRet    rts

; Queue register X <- A. Keeps A, X and Y, as the old direct write did.
QW      sty QSAVY
        ldy QTAIL
        sta QVAL,y
        pha
        txa
        sta QREG,y
        pla
        inc QTAIL
        ldy QSAVY
        rts

; ---------------------------------------------------------------
; One event, and the delay after it if it ends a group
; ---------------------------------------------------------------
Step
        jsr GetByte
        sta EVB
        and #$0f
        sta CH
        lda EVB
        lsr
        lsr
        lsr
        lsr
        and #7
        asl
        tax
        lda EvTab+1,x
        pha
        lda EvTab,x
        pha
        rts                    ; to the handler; it returns to GroupNext
EvTab   !word EvOff-1, EvOn-1, EvBend-1, EvSys-1, EvCtl-1, EvNone-1, EvEnd-1, EvNone-1

GroupNext
        lda EVB
        bpl GNRet              ; more events at this tick
        lda #0                 ; delay, 7 bits a byte, high bit = more
        sta WAITL
        sta WAITH
-       jsr GetByte
        pha
        ldx #7
--      asl WAITL
        rol WAITH
        dex
        bne --
        pla
        pha
        and #$7f
        ora WAITL
        sta WAITL
        pla
        bmi -
-       lda WAITH              ; markers of at most 255 ticks
        bne +
        lda WAITL
        beq GNRet
        ldx #0
        jmp QW
+       ldx #0
        lda #255
        jsr QW
        lda WAITL
        sec
        sbc #255
        sta WAITL
        bcs -
        dec WAITH
        jmp -
GNRet   rts

EvNone  jmp GroupNext
EvEnd   jsr ChannelsInit       ; loop the song
        jmp ScoreInit

EvOff   jsr GetByte
        and #$7f
        sta KEY
        jsr NoteOff
        jmp GroupNext

EvOn    jsr GetByte
        sta TMP
        and #$7f
        sta KEY
        lda TMP
        bpl +
        jsr GetByte
        and #$7f
        ldx CH
        sta C_VEL,x
+       ldx CH
        lda C_VEL,x
        sta VOLX
        jsr NoteOn
        jmp GroupNext

EvBend  jsr GetByte
        lsr
        sec
        sbc #64
        ldx CH
        sta C_BEND,x
        jsr PitchBend
        jmp GroupNext

EvSys   jsr GetByte
        cmp #10
        beq +
        cmp #11
        bne ++
+       jsr AllNotesOff
++      jmp GroupNext

EvCtl   jsr GetByte
        sta CTL
        jsr GetByte
        bpl +
        lda #127
+       sta TMP2
        lda CTL
        bne +
        lda TMP2               ; program change: already a slot number
        ldx CH
        sta C_INS,x
        jmp GroupNext
+       cmp #3
        bne +
        jsr ChannelVolume
+       jmp GroupNext

; ---------------------------------------------------------------
; Score decoder. A frame is a pointer, the bytes it still owes,
; a 16-bit count to skip first, and what is left of a literal run.
; Skipping goes a token at a time - into a literal run is a pointer
; move, past a whole reference is four bytes - and a reference's
; length comes off its parent when it is pushed, so each byte only
; touches the top frame. The first version skipped by decoding and
; walked every frame per byte; busy ticks ran up to 8x over 1 MHz.
; ---------------------------------------------------------------
ScoreInit
        lda #0
        sta FDEPTH
        sta FR_SL
        sta FR_SH
        sta FR_LT
        lda #<Score
        sta FR_PL
        lda #>Score
        sta FR_PH
        lda #<SCORELEN
        sta FR_RL
        lda #>SCORELEN
        sta FR_RH
        rts

GetByte
        ldx FDEPTH
        lda FR_RL,x
        ora FR_RH,x
        bne GBGo
        dec FDEPTH             ; frame done: pop
        jmp GetByte
GBGo    lda FR_LT,x
        beq GBTok
        lda FR_PL,x            ; a byte of the current literal run
        sta P
        lda FR_PH,x
        sta P+1
        ldy #0
        lda (P),y
        sta GB_B
        inc FR_PL,x
        bne +
        inc FR_PH,x
+       dec FR_LT,x
        lda FR_RL,x
        bne +
        dec FR_RH,x
+       dec FR_RL,x
        lda GB_B
        rts

GBTok   lda FR_PL,x
        sta P
        lda FR_PH,x
        sta P+1
        ldy #0
        lda (P),y
        bmi GBRef
        clc                    ; literal run of c+1
        adc #1
        sta TMP
        inc FR_PL,x
        bne +
        inc FR_PH,x
+       lda FR_SL,x
        ora FR_SH,x
        beq GBRun
        lda FR_SH,x            ; k = min(skip, run)
        bne GBAll
        lda FR_SL,x
        cmp TMP
        bcs GBAll
        sta TMP2
        jmp GBSk
GBAll   lda TMP
        sta TMP2
GBSk    lda FR_PL,x            ; pointer += k, run -= k, skip -= k
        clc
        adc TMP2
        sta FR_PL,x
        bcc +
        inc FR_PH,x
+       lda TMP
        sec
        sbc TMP2
        sta TMP
        lda FR_SL,x
        sec
        sbc TMP2
        sta FR_SL,x
        bcs GBRun
        dec FR_SH,x
GBRun   lda TMP
        sta FR_LT,x
        jmp GetByte

GBRef   and #$7f               ; L = MINREF + l
        clc
        adc #MINREF
        sta TL
        iny
        lda (P),y
        clc
        adc #<Score
        sta UL
        iny
        lda (P),y
        adc #>Score
        sta UH
        iny
        lda (P),y
        sta TMP                ; s
        lda FR_PL,x
        clc
        adc #4
        sta FR_PL,x
        bcc +
        inc FR_PH,x
+       lda FR_SH,x            ; skip >= L: step over the whole reference
        bne GBRSkip
        lda FR_SL,x
        cmp TL
        bcc GBRPush
GBRSkip lda FR_SL,x
        sec
        sbc TL
        sta FR_SL,x
        bcs +
        dec FR_SH,x
+       jmp GetByte
GBRPush sta TMP2               ; k = skip, which is now used up
        lda #0
        sta FR_SL,x
        lda TL                 ; n = min(L - k, what the parent owes)
        sec
        sbc TMP2
        sta TH
        lda FR_RH,x
        bne +
        lda FR_RL,x
        cmp TH
        bcs +
        sta TH
+       lda FR_RL,x            ; the parent owes n fewer
        sec
        sbc TH
        sta FR_RL,x
        bcs +
        dec FR_RH,x
+       lda TMP                ; child skips s + k
        clc
        adc TMP2
        sta TL
        lda #0
        adc #0
        sta TMP
        inx
        stx FDEPTH
        lda UL
        sta FR_PL,x
        lda UH
        sta FR_PH,x
        lda TH
        sta FR_RL,x
        lda #0
        sta FR_RH,x
        sta FR_LT,x
        lda TL
        sta FR_SL,x
        lda TMP
        sta FR_SH,x
        jmp GetByte

; ---------------------------------------------------------------
; OPL2 access
; ---------------------------------------------------------------
; The SKpico queues FM writes in a 256-entry ring that its audio core
; drains in real time. Faster than about one write per 20 us and a
; burst laps it, and the ring then reads as empty: every write in it is
; lost. Init plus the first tick did exactly that - instruments never
; loaded, and nothing sounded. So every write is spaced to ~40 us,
; which is also more than the 23 us a real OPL2 needs after data.
OPLW                            ; register X <- A, ~40 us with the call
        stx OPLA
        nop
        nop
        sta OPLD
        sty QSAVY
        ldy #3
-       dey
        bne -
        ldy QSAVY
        rts

OPLInit                         ; as Chocolate Doom's OPL_InitRegisters
        ldx #$40
-       lda #$3f
        jsr OPLW
        inx
        cpx #$56
        bne -
        ldx #$60
-       lda #0
        jsr OPLW
        inx
        cpx #$f6
        bne -
        ldx #$01
-       lda #0
        jsr OPLW
        inx
        cpx #$40
        bne -
        ldx #$04
        lda #$60
        jsr OPLW
        lda #$80
        jsr OPLW
        ldx #$01
        lda #$20
        jmp OPLW

; ---------------------------------------------------------------
; State
; ---------------------------------------------------------------
VoicesInit
        ldx #8
-       lda #$ff
        sta V_CH,x
        sta V_INS,x
        lda #0
        sta V_KEY,x
        sta V_NOTE,x
        sta V_IV,x
        sta V_FL,x
        sta V_FH,x
        sta V_NVOL,x
        sta V_CAR,x
        sta V_MOD,x
        txa
        sta FREEL,x
        dex
        bpl -
        lda #9
        sta NFREE
        lda #0
        sta NALLOC
        rts

ChannelsInit
        ldx #15
-       lda #INITINS
        sta C_INS,x
        lda #100               ; min(music volume 127, 100)
        sta C_VOL,x
        lda #0
        sta C_BEND,x
        lda #127
        sta C_VEL,x
        dex
        bpl -
        rts

; ---------------------------------------------------------------
; Voices
; ---------------------------------------------------------------
GetFree                         ; A = voice, or $ff; carry set if none
        lda NFREE
        bne +
        lda #$ff
        sec
        rts
+       lda FREEL
        pha
        ldx #0
-       cpx NFREE
        beq +
        lda FREEL+1,x
        sta FREEL,x
        inx
        bne -
+       dec NFREE
        pla
        ldx NALLOC
        sta ALLOCL,x
        inc NALLOC
        clc
        rts

Release                         ; II = index in the allocated list
        ldy II
        ldx ALLOCL,y
        stx VV
        lda V_FH,x             ; key off
        pha
        txa
        clc
        adc #$b0
        tax
        pla
        jsr QW
        ldx VV
        lda #$ff
        sta V_CH,x
        lda #0
        sta V_NOTE,x
        ldx II                 ; close the gap
-       inx
        cpx NALLOC
        beq +
        lda ALLOCL,x
        sta ALLOCL-1,x
        jmp -
+       dec NALLOC
        ldx NFREE
        lda VV
        sta FREEL,x
        inc NFREE
        rts

InsPtr                          ; Q = instrument INS, voice IV data (+4+16*IV)
        ldx INS
        lda InsLo,x
        clc
        adc #4
        sta Q
        lda InsHi,x
        adc #0
        sta Q+1
        lda IV
        beq +
        lda Q
        clc
        adc #16
        sta Q
        bcc +
        inc Q+1
+       rts

LoadOp                          ; X = operator, Y = offset of its 6 bytes in Q, C = max level
        stx TMP
        php
        tya
        clc
        adc #4
        tay
        lda (Q),y              ; scale
        plp
        bcc +
        ora #$3f
        jmp ++
+       iny
        ora (Q),y              ; | level
        dey
++      sta CAR                ; the level written
        lda TMP
        clc
        adc #$40
        tax
        lda CAR
        jsr QW
        dey                    ; back to tremolo
        dey
        dey
        dey
        lda TMP
        clc
        adc #$20
        tax
        lda (Q),y
        jsr QW
        iny
        lda TMP
        clc
        adc #$60
        tax
        lda (Q),y
        jsr QW
        iny
        lda TMP
        clc
        adc #$80
        tax
        lda (Q),y
        jsr QW
        iny
        lda TMP
        clc
        adc #$e0
        tax
        lda (Q),y
        jmp QW

SetInstrument                   ; voice VV <- INS, IV
        ldx VV
        lda V_INS,x
        cmp INS
        bne +
        lda V_IV,x
        cmp IV
        bne +
        rts
+       lda INS
        sta V_INS,x
        lda IV
        sta V_IV,x
        jsr InsPtr
        ldx VV                 ; carrier first, at maximum level
        lda Op2,x
        tax
        ldy #7
        sec
        jsr LoadOp
        ldx VV
        lda CAR
        sta V_CAR,x
        ldy #6                 ; modulator: maximum level unless modulating
        lda (Q),y
        lsr                    ; C = feedback bit 0 = not modulating
        lda Op1,x
        tax
        ldy #0
        jsr LoadOp
        ldx VV
        lda CAR
        sta V_MOD,x
        ldy #6
        lda (Q),y
        ora #$30
        pha
        txa
        clc
        adc #$c0
        tax
        pla
        jmp QW

SetVolume                       ; voice VV, note volume VOLX
        ldx VV
        lda VOLX
        sta V_NVOL,x
        lda V_INS,x
        sta INS
        lda V_IV,x
        sta IV
        jsr InsPtr
        ldx VV
        ldy V_CH,x
        ldx C_VOL,y
        lda VolMap,x
        clc
        adc #1
        sta MUL2               ; VolMap[channel] + 1, at most 128
        ldx VOLX
        lda VolMap,x
        sta MUL1
        jsr Mul8               ; A = (MUL1 * MUL2) >> 8
        sta TMP
        lda #$3f
        sec
        sbc TMP
        sta CAR                ; carrier level wanted
        ldx VV
        lda V_CAR,x
        and #$3f
        cmp CAR
        bne +
        rts
+       lda V_CAR,x
        and #$c0
        ora CAR
        sta V_CAR,x
        pha
        lda Op2,x
        clc
        adc #$40
        tax
        pla
        jsr QW
        ldy #6                 ; non-modulated: the modulator follows
        lda (Q),y
        and #1
        bne +
        rts
+       ldy #5
        lda (Q),y              ; modulator level
        cmp #$3f
        bne +
        rts
+       cmp CAR
        bcs +
        lda CAR
+       sta TMP
        ldx VV
        lda V_MOD,x
        and #$c0
        ora TMP
        cmp V_MOD,x
        bne +
        rts
+       sta V_MOD,x
        sta TMP
        ldy #4
        lda (Q),y              ; modulator scale
        and #$c0
        ora TMP
        pha
        lda Op1,x
        clc
        adc #$40
        tax
        pla
        jmp QW

Mul8                            ; A = (MUL1 * MUL2) >> 8
        lda #0
        ldx #8
        lsr MUL1
-       bcc +
        clc
        adc MUL2
+       ror
        ror MUL1
        dex
        bne -
        rts

Frequency                       ; voice VV -> UL/UH
        ldx VV
        lda V_INS,x
        sta INS
        lda V_IV,x
        sta IV
        jsr InsPtr
        ldx VV
        lda V_NOTE,x
        sta TL
        lda #0
        sta TH                 ; note, signed 16-bit
        ldx INS
        lda InsLo,x
        sta P
        lda InsHi,x
        sta P+1
        ldy #0
        lda (P),y              ; flags
        and #1
        bne FqWrap             ; fixed pitch: no offset
        ldy #14
        lda (Q),y
        clc
        adc TL
        sta TL
        iny
        lda (Q),y
        adc TH
        sta TH
FqWrap  lda TH                 ; while note < 0: += 12
        bpl FqChk95
        lda TL
        clc
        adc #12
        sta TL
        bcc FqWrap
        inc TH
        jmp FqWrap
FqChk95 lda TH                 ; while note > 95: -= 12
        bne FqDown
        lda TL
        cmp #96
        bcc FqIdx
FqDown  lda TL
        sec
        sbc #12
        sta TL
        bcs FqChk95
        dec TH
        jmp FqChk95
FqIdx   lda #0                 ; idx = 64 + 32 * note + bend
        sta UH
        lda TL
        ldx #5
-       asl
        rol UH
        dex
        bne -
        clc
        adc #64
        sta UL
        bcc +
        inc UH
+       ldx VV
        ldy V_CH,x
        lda C_BEND,y
        bpl +
        dec UH
+       clc
        adc UL
        sta UL
        bcc +
        inc UH
+       lda IV                 ; second voice: + fine_tuning/2 - 64
        beq FqClamp
        ldy #2
        lda (P),y
        lsr
        sec
        sbc #64
        bpl +
        dec UH
+       clc
        adc UL
        sta UL
        bcc FqClamp
        inc UH
FqClamp lda UH
        bpl +
        lda #0
        sta UL
        sta UH
+       lda UH                 ; idx < 284?
        cmp #>284
        bcc FqLow
        bne FqHigh
        lda UL
        cmp #<284
        bcs FqHigh
FqLow   lda #<FreqLo
        clc
        adc UL
        sta P
        lda #>FreqLo
        adc UH
        sta P+1
        ldy #0
        lda (P),y
        pha
        ldx #1                 ; high byte: the curve rises through each
        lda UH                 ; segment, so it is 1 below 283, 2 below
        cmp #>283              ; 508, then 3 - no table needed
        bcc FqHi
        bne +
        lda UL
        cmp #<283
        bcc FqHi
+       inx
        lda UH
        cmp #>508
        bcc FqHi
        bne +
        lda UL
        cmp #<508
        bcc FqHi
+       inx
FqHi    stx UH
        pla
        sta UL
        rts
FqHigh  lda UL                 ; idx - 284, then octaves of 384
        sec
        sbc #<284
        sta UL
        lda UH
        sbc #>284
        sta UH
        lda #0
        sta TMP                ; octave
-       lda UH
        cmp #>384
        bcc +
        bne ++
        lda UL
        cmp #<384
        bcc +
++      lda UL
        sec
        sbc #<384
        sta UL
        lda UH
        sbc #>384
        sta UH
        inc TMP
        jmp -
+       lda TMP
        cmp #8
        bcc +
        lda #7
+       asl
        asl
        sta TMP                ; octave << 10 = (octave << 2) in the high byte
        lda UL
        clc
        adc #<284
        sta UL
        lda UH
        adc #>284
        sta UH
        jsr FqLow
        lda UH
        ora TMP
        sta UH
        rts

UpdateFreq                      ; voice VV
        jsr Frequency
        ldx VV
        lda UL
        cmp V_FL,x
        bne +
        lda UH
        cmp V_FH,x
        bne +
        rts
+       lda UL
        sta V_FL,x
        lda UH
        sta V_FH,x
        txa
        clc
        adc #$a0
        tax
        lda UL
        jsr QW
        txa
        clc
        adc #$10               ; $b0 + voice
        tax
        lda UH
        ora #$20
        jmp QW

VoiceKeyOn                      ; channel CH, INS, IV, NOTE, KEY, VOLX
        jsr GetFree
        bcc +
        rts
+       sta VV
        tax
        lda CH
        sta V_CH,x
        lda KEY
        sta V_KEY,x
        ldx INS
        lda InsLo,x
        sta P
        lda InsHi,x
        sta P+1
        ldy #0
        lda (P),y
        and #1
        beq +
        ldy #3
        lda (P),y              ; fixed note
        jmp ++
+       lda NOTE
++      ldx VV
        sta V_NOTE,x
        jsr SetInstrument
        jsr SetVolume
        ldx VV
        lda #0
        sta V_FL,x
        sta V_FH,x
        jmp UpdateFreq

Replace                         ; Doom v1.9: steal a voice
        lda #0
        sta TMP                ; result index
        ldx #0
-       cpx NALLOC
        beq +
        ldy ALLOCL,x
        lda V_IV,y
        bne ++
        lda V_CH,y
        sta TMP2
        ldy TMP
        lda ALLOCL,y
        tay
        lda TMP2
        cmp V_CH,y
        bcc +++
++      stx TMP
+++     inx
        jmp -
+       lda TMP
        sta II
        jmp Release

NoteOff                         ; CH, KEY
        lda #0
        sta II
-       lda II
        cmp NALLOC
        bcs ++
        tay
        ldx ALLOCL,y
        lda V_CH,x
        cmp CH
        bne +
        lda V_KEY,x
        cmp KEY
        bne +
        jsr Release
        jmp -
+       inc II
        jmp -
++      rts

AllNotesOff                     ; CH
        lda #0
        sta II
-       lda II
        cmp NALLOC
        bcs ++
        tay
        ldx ALLOCL,y
        lda V_CH,x
        cmp CH
        bne +
        jsr Release
        jmp -
+       inc II
        jmp -
++      rts

NoteOn                          ; CH, KEY, VOLX
        lda VOLX
        bne +
        jmp NoteOff
+       lda KEY
        sta NOTE
        lda CH
        cmp #15
        bne NOMel
        lda KEY
        cmp #35
        bcc NORet
        cmp #82
        bcs NORet
        sec
        sbc #35
        tax
        lda PercMap,x
        sta INS
        lda #60
        sta NOTE
        jmp NOGo
NORet   rts
NOMel   ldx CH
        lda C_INS,x
        sta INS
NOGo    lda NFREE
        bne +
        jsr Replace
+       lda #0
        sta IV
        jsr VoiceKeyOn
        ldx INS
        lda InsLo,x
        sta P
        lda InsHi,x
        sta P+1
        ldy #0
        lda (P),y
        and #4                 ; double voice
        bne +
        rts
+       lda #1
        sta IV
        jmp VoiceKeyOn

ChannelVolume                   ; CH, TMP2 = volume
        ldx CH
        lda TMP2
        sta C_VOL,x            ; music volume 127 never clips it
        lda #0
        sta VV
-       ldx VV
        lda V_CH,x
        cmp CH
        bne +
        lda V_NVOL,x
        sta VOLX
        jsr SetVolume
+       inc VV
        lda VV
        cmp #9
        bne -
        rts

PitchBend                       ; CH; bend already stored
        lda #0
        sta NK
        sta NM
        sta II
-       ldx II
        cpx NALLOC
        beq ++
        lda ALLOCL,x
        sta VV
        tax
        lda V_CH,x
        cmp CH
        bne +
        jsr UpdateFreq
        ldx NM
        lda VV
        sta MOVED,x
        inc NM
        inc II
        jmp -
+       ldx NK
        lda VV
        sta KEPT,x
        inc NK
        inc II
        jmp -
++      ldx #0                 ; allocated = kept, then moved
        ldy #0
-       cpy NK
        beq +
        lda KEPT,y
        sta ALLOCL,x
        inx
        iny
        jmp -
+       ldy #0
-       cpy NM
        beq +
        lda MOVED,y
        sta ALLOCL,x
        inx
        iny
        jmp -
+       rts

Op1     !byte $00, $01, $02, $08, $09, $0a, $10, $11, $12
Op2     !byte $03, $04, $05, $0b, $0c, $0d, $13, $14, $15

; ---------------------------------------------------------------
; SKpico: SID #2 = FM (byte 8 = 4) at the IO pad (byte 10 = 5), in
; RAM only - as the SID driver's setup, and it leaves config mode
; before anything else touches the chip.
; ---------------------------------------------------------------
SKFM    lda #$ff
        sta SK_MODE
        lda #0
        sta SK_PTR
        tax
-       lda SK_DATA
        sta SKBuf,x
        inx
        cpx #64
        bne -
        lda SKBuf              ; 64 of one byte: nothing answered
        ldx #1
-       cmp SKBuf,x
        bne +
        inx
        cpx #64
        bne -
        beq SKLapse
+       ldx #59
-       lda SKBuf,x
        cmp #$fa
        bcs SKLapse
        dex
        bpl -
        lda #4
        sta SKBuf+8
        lda #5
        sta SKBuf+10
        lda #0
        sta SK_PTR
        tax
-       lda SKBuf,x
        sta SK_DATA
        inx
        cpx #60
        bne -
        lda #$fe
        sta SK_DATA
        ldx #40                ; ~50 ms for the firmware to switch
        jmp SKDelay
SKLapse ldx #25
SKDelay
--      ldy #0
-       dey
        bne -
        dex
        bne --
        rts
SKBuf   !fill 64, 0
