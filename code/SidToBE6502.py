r"""
Run with  py -3  on this machine: plain `python` is MSYS2's and cannot
open a D:\... script path. No shebang here on purpose - the py launcher
honours them, and "/usr/bin/env python" points back at MSYS.

Convert a .sid file (PSID/RSID, e.g. from the High Voltage SID Collection)
into a binary that runs on the Ben Eater 6502.

REQUIRES THE $D400 DECODE. This tool does not touch the tune's code at all,
so the SID must answer at its native $D400. See MEMORY_MAP.md for the
decode that puts it at both $4800 and $D400.

Why no address patching: a .sid file is a compiled blob. Finding every
SID access in a binary means disassembling it, and you would still miss
any that are computed, self-modified, or indexed off a pointer. Porting
from source works because the addresses are text; here they are not.

The tune is emitted at its own load address, followed by a driver that
polls the VIA T1 flag - no IRQ, no NMI, no WAI. When there is room below
$4000 the driver also shows the tune's title and author on an HD44780 at
$4C00; when there is not, it falls back to the bare 37-byte driver rather
than refusing the tune.

The driver also sets up the SIDKick pico to match the tune before calling
init: chip model (6581/8580), PAL/NTSC clock, and SID #2 for 2SID tunes,
all from the header. It is applied in RAM only and never saved, so a power
cycle puts back whatever SKConfig last saved to flash. A field the header
leaves unknown is left as it is, and an 8580 tune keeps digiboost if it is
already on. If the chip does not answer, or a config byte is $FA or above
(a command, so it cannot be written back), the setup is skipped and the
tune plays on whatever is set. --no-skpico leaves the driver out and just
reports what to set by hand.

2SID tunes need SID #2 where the SKpico can put it - $D420 (A5 pad),
$D500 (A8) or $D520 (both) - and the pads wired; WIRED_PADS says which
are. 3SID tunes cannot play: the SKpico emulates two.

Usage:
  python SidToBE6502.py <file.sid> [song] [rate_hz] [--no-skpico]

  song     0-based song index. Default is the file's own start song.
  rate_hz  override the call rate. Default comes from the PSID speed
           field: the frame rate for a vsync song, 50 Hz PAL or 60 Hz
           NTSC, and 60 Hz for a CIA-timed one (a guess - CIA tunes set
           their own period, which the header does not record).

Output: BE6502_<name>[_songN].bin, plus the WozMon load and run addresses.
"""
import io, os, re, shutil, struct, subprocess, sys

ACME = (os.environ.get('ACME') or shutil.which('acme')
        or r"D:/Documents/Arduino/Assembly/acme/acme.exe")
RAM_TOP = 0x4000          # RAM is decoded only for A15=0 and A14=0

# SKpico SID #2 address setting (config byte 10) for each place a 2SID tune
# can put its second SID, and the SKpico address pads that setting needs.
# The GAL already selects the SKpico across $D400-$D7FF; the pads are what
# let it tell $D420 or $D500 from $D400. $DE00/$DF00 are not decoded here.
SID2_SLOTS = {0xD420: (1, ('A5',)), 0xD500: (2, ('A8',)), 0xD520: (3, ('A5', 'A8'))}
WIRED_PADS = ('A5',)      # pads wired to the CPU bus on this machine


def be16(b, o):
    return struct.unpack('>H', b[o:o + 2])[0]


def text(b, o):
    return b[o:o + 32].split(b'\x00')[0].decode('latin-1').strip()


def header_info(raw):
    """Chip model, clock and extra SIDs from a PSID/RSID header. The flags
    word arrived in v2, the second SID address in v3, the third in v4."""
    version = be16(raw, 0x04)
    flags = be16(raw, 0x76) if version >= 2 and len(raw) >= 0x78 else 0

    def sid_at(o, since):
        if version < since or len(raw) <= o or raw[o] == 0:
            return None
        return 0xD000 + raw[o] * 16

    models = ('unknown', '6581', '8580', '6581 or 8580')
    return {
        'clock': ('unknown', 'PAL', 'NTSC', 'PAL or NTSC')[flags >> 2 & 3],
        'model': models[flags >> 4 & 3],
        'model2': models[flags >> 6 & 3],     # unknown means same as SID #1
        'sid2': sid_at(0x7A, 3),
        'sid3': sid_at(0x7B, 4),
    }


def describe(info):
    """Short tag for listings, e.g. '6581 PAL' or '8580 PAL 2SID $D420'."""
    out = '%s %s' % (info['model'], info['clock'])
    if info['sid3']:
        out += ' 3SID'
    elif info['sid2']:
        out += ' 2SID $%04X' % info['sid2']
    return out


def sid2_problem(info):
    """Why this machine cannot give the tune its extra SIDs, or None."""
    if info['sid3']:
        return ('3SID tune (third SID at $%04X): the SKpico emulates two.'
                % info['sid3'])
    if info['sid2'] is None:
        return None
    slot = SID2_SLOTS.get(info['sid2'])
    if slot is None:
        return ('second SID at $%04X: the SKpico can only put SID #2 at '
                '$D420, $D500 or $D520 here.' % info['sid2'])
    missing = [p for p in slot[1] if p not in WIRED_PADS]
    if missing:
        return ('second SID at $%04X needs CPU %s wired to the SKpico\'s '
                '%s pad%s.' % (info['sid2'], ' and '.join(missing),
                                 ' and '.join(missing), 's' if len(missing) > 1 else ''))
    return None


def skpico_settings(info):
    """(sid1, clock, sid2_type, sid2_addr) for the SKpico, None = leave it."""
    chip = {'6581': 0, '8580': 1}
    sid1 = chip.get(info['model'])
    clock = {'PAL': 0, 'NTSC': 1}.get(info['clock'])
    if info['sid2']:
        return sid1, clock, chip.get(info['model2']), SID2_SLOTS[info['sid2']][0]
    return sid1, clock, 3, None             # single SID: SID #2 off


def settings_text(cfg):
    sid1, clock, sid2_type, sid2_addr = cfg
    parts = ['SID #1 %s' % {None: 'unchanged', 0: '6581', 1: '8580'}[sid1],
             {None: 'clock unchanged', 0: 'PAL', 1: 'NTSC'}[clock]]
    if sid2_type == 3:
        parts.append('SID #2 off')
    else:
        parts.append('SID #2 %s at setting %d' % (
            {None: 'same chip as #1', 0: '6581', 1: '8580'}[sid2_type], sid2_addr))
    return ', '.join(parts)


def skpico_source(cfg):
    """Driver code that reads the SKpico config, changes the fields in cfg,
    and applies it in RAM. Leaves config mode before returning either way:
    while it lasts, writes to $D414-$D41C are commands (one launches a
    program), and those are ordinary SID registers to the tune's init."""
    sid1, clock, sid2_type, sid2_addr = cfg
    o = ['SKSetup',
         '        lda #$ff',
         '        sta SK_MODE            ; $1F: config mode',
         '        lda #$00',
         '        sta SK_PTR             ; $1E: config byte 0',
         '        tax',
         '-       lda SK_DATA            ; read all 64, pointer advances',
         '        sta SKBuf,x',
         '        inx',
         '        cpx #64',
         '        bne -',
         '        lda SKBuf              ; 64 copies of one byte: nothing answered',
         '        ldx #1',
         '-       cmp SKBuf,x',
         '        bne +',
         '        inx',
         '        cpx #64',
         '        bne -',
         '        beq SKLapse',
         '+       ldx #59                ; $FA and up are commands on $1D,',
         '-       lda SKBuf,x            ; so such a byte cannot go back',
         '        cmp #$fa',
         '        bcs SKLapse',
         '        dex',
         '        bpl -']
    if sid1 == 0:
        o += ['        lda #0                 ; SID #1 6581',
              '        sta SKBuf+0']
    elif sid1 == 1:
        o += ['        lda SKBuf+0            ; SID #1 8580, keeping digiboost (2)',
              '        cmp #2',
              '        beq +',
              '        lda #1',
              '        sta SKBuf+0',
              '+']
    if clock is not None:
        o += ['        lda #%d                 ; clock: 0 PAL, 1 NTSC' % clock,
              '        sta SKBuf+59']
    if sid2_type is None:
        o += ['        lda SKBuf+0            ; SID #2 the same chip as SID #1']
    else:
        o += ['        lda #%d                 ; SID #2 type%s'
              % (sid2_type, ', 3 = none' if sid2_type == 3 else '')]
    o += ['        sta SKBuf+8']
    if sid2_addr is not None:
        o += ['        lda #%d                 ; SID #2 address setting' % sid2_addr,
              '        sta SKBuf+10']
    o += ['        lda #$00               ; still in config mode: rewind',
          '        sta SK_PTR',
          '        tax',
          '-       lda SKBuf,x            ; bytes 0-59, through the clock',
          '        sta SK_DATA',
          '        inx',
          '        cpx #60',
          '        bne -',
          '        lda #$fe               ; apply, RAM only, ends config mode',
          '        sta SK_DATA',
          '        ldx #4                 ; let the firmware settle, ~5 ms',
          '        jmp SKDelay',
          'SKLapse                        ; let config mode time out instead',
          '        ldx #25                ; ~32 ms > 25000 cycles',
          'SKDelay                        ; X x ~1.28 ms',
          '--      ldy #0',
          '-       dey',
          '        bne -',
          '        dex',
          '        bne --',
          '        rts',
          'SKBuf   !fill 64, 0']
    return '\n'.join(o) + '\n'


def quotable(s):
    """Trim to something safe to put inside an ACME string literal."""
    out = ''.join(c if 32 <= ord(c) < 127 and c not in '"\\' else ' ' for c in s)
    return out[:16].ljust(16)


# ---------------------------------------------------------------------------
# HD44780 in 4-bit mode through a 74LS574 latch at $4C00.
#   Q0-Q3 -> D4-D7, Q4 -> RS, Q5 -> E, LCD RW grounded.
# Plain 6502 - Y is saved in zero page rather than with PHY, so this
# assembles for any CPU the tune might have been written for.
# ---------------------------------------------------------------------------
LCD_SOURCE = """
LCDNib
        and #$0f
        ora LCDRS
        sta LCDTMP
        sta LCD                 ; data valid, E low
        ora #LCD_E
        sta LCD                 ; E high
        lda LCDTMP
        sta LCD                 ; E low -> latched
        rts

LCDByte
        sty LCDYSAV
        pha
        lsr
        lsr
        lsr
        lsr
        jsr LCDNib              ; high nibble
        pla
        jsr LCDNib              ; low nibble
        jsr LCDDly40
        ldy LCDYSAV
        rts

LCDCmd  pha
        lda #$00
        sta LCDRS
        pla
        jmp LCDByte

LCDChar pha
        lda #LCD_RS
        sta LCDRS
        pla
        jmp LCDByte

LCDLine1
        lda #$80
        jmp LCDCmd
LCDLine2
        lda #$c0
        jmp LCDCmd

LCDPrint
        ldy #$00
lcdpr1  lda (LCDPTR),y
        beq lcdpr2
        jsr LCDChar
        iny
        bne lcdpr1
lcdpr2  rts

LCDInit
        ldx #40                 ; >15ms after power-up
        jsr LCDDlyMS
        lda #$00
        sta LCDRS
        lda #$03                ; sent blind, still in 8-bit mode
        jsr LCDNib
        ldx #4
        jsr LCDDlyMS
        lda #$03
        jsr LCDNib
        jsr LCDDly200
        lda #$03
        jsr LCDNib
        jsr LCDDly200
        lda #$02                ; switch to 4-bit
        jsr LCDNib
        jsr LCDDly200
        lda #$28                ; 4-bit, 2 lines, 5x8
        jsr LCDCmd
        lda #$08                ; display off
        jsr LCDCmd
        lda #$01                ; clear
        jsr LCDCmd
        ldx #4
        jsr LCDDlyMS
        lda #$06                ; entry mode: increment
        jsr LCDCmd
        lda #$0c                ; display on, cursor off
        jmp LCDCmd

LCDDly40
        ldy #7
lcdd40  dey
        bne lcdd40
        rts
LCDDly200
        ldy #38
lcdd200 dey
        bne lcdd200
        rts
LCDDlyMS                        ; X x ~1.28ms
lcddms1 ldy #$00
lcddms2 dey
        bne lcddms2
        dex
        bne lcddms1
        rts
"""


def build_asm(datfile, org, init, play, song, rate, title, author, use_lcd, skcfg):
    n = int(round(1000000.0 / rate)) - 2       # VIA T1 free-run period is N+2
    if not 0 <= n <= 0xFFFF:
        raise SystemExit('rate %s Hz is out of range for a 16-bit T1 at 1 MHz' % rate)

    o = []
    o.append(';  generated by SidToBE6502.py - do not edit')
    o.append(';  load $%04X  init $%04X  play $%04X  song %d  %g Hz%s'
             % (org, init, play, song, rate, '  + LCD' if use_lcd else ''))
    o.append('!cpu 6502')
    o.append('VIA_ACR  = $600b')
    o.append('VIA_T1CL = $6004')
    o.append('VIA_T1CH = $6005')
    o.append('VIA_IFR  = $600d')
    if skcfg:
        o.append('SK_DATA  = $d41d')
        o.append('SK_PTR   = $d41e')
        o.append('SK_MODE  = $d41f')
    if use_lcd:
        o.append('LCD      = $4c00')
        o.append('LCD_RS   = $10')
        o.append('LCD_E    = $20')
        o.append('LCDPTR   = $fa')
        o.append('LCDRS    = $fc')
        o.append('LCDTMP   = $fd')
        o.append('LCDYSAV  = $fe')
    o.append('')
    o.append('* = $%04X' % org)
    o.append('!binary "%s"' % os.path.basename(datfile))
    o.append('')
    o.append('BE6502_START')
    o.append('        sei')
    if skcfg:
        o.append('        jsr SKSetup        ; SKpico to suit the tune, RAM only')
    if use_lcd:
        o.append('        jsr LCDInit')
        o.append('        jsr LCDLine1')
        o.append('        lda #<TitleStr')
        o.append('        sta LCDPTR')
        o.append('        lda #>TitleStr')
        o.append('        sta LCDPTR+1')
        o.append('        jsr LCDPrint')
        o.append('        jsr LCDLine2')
        o.append('        lda #<AuthorStr')
        o.append('        sta LCDPTR')
        o.append('        lda #>AuthorStr')
        o.append('        sta LCDPTR+1')
        o.append('        jsr LCDPrint')
    o.append('        lda #$40           ; ACR: T1 free-run, PB7 disabled')
    o.append('        sta VIA_ACR')
    o.append('        lda #$%02X           ; %d -> N+2 = %d cycles = %g Hz'
             % (n & 0xFF, n, n + 2, rate))
    o.append('        sta VIA_T1CL')
    o.append('        lda #$%02X' % (n >> 8))
    o.append('        sta VIA_T1CH       ; loads latches and starts the timer')
    o.append('        lda #$%02X           ; song number' % song)
    o.append('        tax')
    o.append('        tay')
    o.append('        jsr $%04X          ; tune init' % init)
    o.append('BE6502_LOOP')
    o.append('        jsr $%04X          ; tune play' % play)
    o.append('-       bit VIA_IFR        ; V flag <- bit 6 = T1 timed out')
    o.append('        bvc -')
    o.append('        bit VIA_T1CL       ; reading T1C_L clears the flag')
    o.append('        jmp BE6502_LOOP')
    if skcfg:
        o.append(skpico_source(skcfg))
    if use_lcd:
        o.append(LCD_SOURCE)
        o.append('TitleStr  !text "%s"' % quotable(title))
        o.append('          !byte 0')
        o.append('AuthorStr !text "%s"' % quotable(author))
        o.append('          !byte 0')
    return '\n'.join(o) + '\n'


def convert(path, song=None, rate=None, skpico=True):
    raw = io.open(path, 'rb').read()
    magic = raw[:4]
    if magic not in (b'PSID', b'RSID'):
        raise SystemExit('not a PSID/RSID file (magic %r)' % magic)

    version = be16(raw, 0x04)
    data_off = be16(raw, 0x06)
    load = be16(raw, 0x08)
    init = be16(raw, 0x0A)
    play = be16(raw, 0x0C)
    nsongs = be16(raw, 0x0E)
    start = be16(raw, 0x10)
    speed = struct.unpack('>I', raw[0x12:0x16])[0]
    title, author = text(raw, 0x16), text(raw, 0x36)
    info = header_info(raw)

    data = raw[data_off:]
    if load == 0:                       # load address is the first two data bytes
        load = struct.unpack('<H', data[:2])[0]
        data = data[2:]
    if init == 0:
        init = load

    print('  %-12s %s' % ('title', title))
    print('  %-12s %s' % ('author', author))
    print('  %-12s %s' % ('released', text(raw, 0x56)))
    print('  %-12s %s v%d, %d song%s, starts on %d'
          % ('format', magic.decode(), version, nsongs, 's' if nsongs != 1 else '', start))
    print('  %-12s load $%04X-$%04X  init $%04X  play $%04X'
          % ('addresses', load, load + len(data) - 1, init, play))
    print('  %-12s %s, %s' % ('written for', info['model'], info['clock']))
    if info['sid2']:
        print('  %-12s second SID at $%04X, %s'
              % ('2SID', info['sid2'],
                 'same chip as #1' if info['model2'] == 'unknown' else info['model2']))
    if info['sid3']:
        print('  %-12s third SID at $%04X' % ('3SID', info['sid3']))

    song = (start - 1) if song is None else song   # header start song is 1-based
    song = max(0, min(song, nsongs - 1))
    cia = bool(speed >> song & 1) if song < 32 else False
    if rate is None:
        rate = 60.0 if cia or info['clock'] == 'NTSC' else 50.0
    print('  %-12s song %d of %d, %s-timed, calling play at %g Hz'
          % ('playback', song, nsongs, 'CIA' if cia else 'vsync', rate))

    problems = []
    if play == 0:
        problems.append('play address is 0: the tune installs its own IRQ handler '
                        'and drives itself. The polled driver cannot run it.')
    if magic == b'RSID':
        problems.append('RSID files expect a real C64 with the KERNAL present. '
                        'PSID is the format that promises to run standalone.')
    end = load + len(data)
    if end > RAM_TOP:
        problems.append('tune occupies $%04X-$%04X, past the top of RAM ($%04X). '
                        'A binary cannot be relocated.' % (load, end - 1, RAM_TOP))
    if load < 0x0200:
        problems.append('tune loads at $%04X, over zero page and the stack.' % load)
    if sid2_problem(info):
        problems.append(sid2_problem(info))
    if problems:
        for p in problems:
            print('  PROBLEM      %s' % p)
        raise SystemExit('cannot convert this one.')

    name = 'BE6502_' + re.sub(r'[^A-Za-z0-9]+', '_',
                              os.path.splitext(os.path.basename(path))[0]).strip('_')
    if song:
        name += '_song%d' % song
    datfile = name + '.dat'
    io.open(datfile, 'wb').write(bytes(data))

    skcfg = skpico_settings(info)
    # Most wanted first: the SKpico setup decides whether the tune sounds
    # right, the LCD is only a label. A 2SID tune is pointless without it.
    variants = [(True, True), (False, True), (False, False)] if skpico else [(True, False), (False, False)]
    if info['sid2'] and skpico:
        variants = variants[:2]
    try:
        for n, (use_lcd, use_sk) in enumerate(variants):
            last = n == len(variants) - 1
            asm = build_asm(datfile, load, init, play, song, rate, title, author,
                            use_lcd, skcfg if use_sk else None)
            io.open(name + '.asm', 'w', newline='\r\n').write(asm)
            r = subprocess.Popen([ACME, '-f', 'plain', '-o', name + '.bin',
                                  '--symbollist', name + '.sym', name + '.asm'],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            log = r.communicate()[0].decode('utf-8', 'replace')
            if r.returncode != 0:
                if last:
                    raise SystemExit('acme failed:\n' + log.strip()[:600])
                continue
            size = os.path.getsize(name + '.bin')
            if load + size <= RAM_TOP:
                break
            if last:
                raise SystemExit('no room for the driver below $%04X' % RAM_TOP)
    finally:
        if os.path.exists(datfile):
            os.remove(datfile)

    if not use_lcd:
        print('  %-12s no room for the LCD code' % 'note')
    if use_sk:
        print('  %-12s sets %s - RAM only, at start' % ('SKpico', settings_text(skcfg)))
    else:
        print('  %-12s %s: set %s by hand with SKConfig'
              % ('SKpico', 'left out' if not skpico else 'no room for the setup',
                 settings_text(skcfg)))

    entry = None
    for l in io.open(name + '.sym', encoding='utf-8', errors='replace'):
        if l.strip().startswith('BE6502_START'):
            entry = re.search(r'\$([0-9a-fA-F]+)', l).group(1).upper()
    size = os.path.getsize(name + '.bin')
    print('  %-12s %s%s  (%d bytes, ends $%04X)'
          % ('written', name + '.bin', '  + LCD' if use_lcd else '', size, load + size - 1))
    print('  %-12s %04XL   then   %sR' % ('WozMon', load, entry))
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if set(flags) - {'--no-skpico'} or not args:
        print(__doc__)
        return 2
    song = int(args[1]) if len(args) > 1 else None
    rate = float(args[2]) if len(args) > 2 else None
    return convert(args[0], song, rate, skpico='--no-skpico' not in flags)


if __name__ == '__main__':
    sys.exit(main())
