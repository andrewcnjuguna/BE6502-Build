# SPDX-License-Identifier: GPL-2.0-or-later
r"""
Build a BE6502 binary that plays a Doom music lump on the SIDKick pico's
OPL2 - Doom's own sound driver logic running on the 6502.

    python3 doomplay.py DOOM1.WAD D_E1M1          writes DoomPlay_D_E1M1.bin
    python3 be6502.py load DoomPlay_D_E1M1.bin --run

Needs GAL rev 02 (OPL2 at $5420/$5430). The player switches the SKpico's
SID #2 to FM by itself, in RAM only, so no SKConfig step.

GPL-2.0-or-later like doomopl.py, whose port of Chocolate Doom's OPL
driver DoomPlay.asm reimplements in 6502 code. Doom's data never goes
into the repository: the WAD is read at build time.

Why the music has to be packed: E1M1 as OPL2 register writes is 37,504
of them - over 75 KB, and the driver rotates voices, so repeats of a riff
do not repeat as writes. The MUS score does repeat, so the 6502 runs the
driver and the score is compressed instead: back-references into the
compressed stream itself, which the player follows with a small stack
of frames, never needing the decoded score in memory. 17,237 bytes of
E1M1 become about 10 KB.

The score is lightly rewritten first, without changing what it plays:
melodic channels are renumbered in order of first use, as mus2mid does,
so the driver's channel number is the MUS one; program changes name the
instrument's slot among those the song uses.

    python3 doomplay.py DOOM1.WAD D_E1M1 --check   ...and compare, tick by
tick, every OPL2 write the 6502 code makes in py65 against doomopl.py.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

import doomopl
from doomopl_tables import FREQUENCY, VOLUME

MINREF = 5
MAXREF = MINREF + 127
ACME = os.environ.get('ACME') or shutil.which('acme') or r"D:/Documents/Arduino/Assembly/acme/acme.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
LOAD = 0x0300
TICK_HZ = 140


def events(score):
    """Split a MUS score into (event bytes, delay bytes) groups, byte for byte."""
    pos = 0
    out = []
    while pos < len(score):
        group = []
        while True:
            start = pos
            b = score[pos]
            pos += 1
            kind = (b >> 4) & 7
            if kind in (0, 2, 3):
                pos += 1
            elif kind == 1:
                pos += 1
                if score[pos - 1] & 0x80:
                    pos += 1
            elif kind == 4:
                pos += 2
            group.append(bytearray(score[start:pos]))
            if b & 0x80 or kind == 6:
                break
        if group[-1][0] & 0x70 == 0x60:
            out.append((group, b''))
            break
        dstart = pos
        while score[pos] & 0x80:
            pos += 1
        pos += 1
        out.append((group, score[dstart:pos]))
    return out


def rewrite(score, main_index):
    """Renumber melodic channels by first use and program changes to slots."""
    chmap, nxt = {15: 15}, 0
    programs = [0]
    percs = []
    for group, _ in events(score):
        for ev in group:
            c = ev[0] & 15
            if c not in chmap:
                if nxt == 9:
                    nxt += 1
                chmap[c] = nxt
                nxt += 1
            ev[0] = (ev[0] & 0xF0) | chmap[c]
            kind = (ev[0] >> 4) & 7
            if kind == 4 and ev[1] == 0 and ev[2] not in programs:
                programs.append(ev[2])
            if kind == 1 and chmap[c] == 15 and 35 <= (ev[1] & 0x7F) <= 81 \
                    and (ev[1] & 0x7F) not in percs:
                percs.append(ev[1] & 0x7F)
    slots = [p for p in programs] + [128 + k - 35 for k in sorted(percs)]
    out = bytearray()
    for group, delay in events(score):
        for ev in group:
            ev = bytearray(ev)
            c = ev[0] & 15
            ev[0] = (ev[0] & 0xF0) | chmap[c]
            if (ev[0] >> 4) & 7 == 4 and ev[1] == 0:
                ev[2] = slots.index(ev[2])
            out += ev
        out += delay
    return bytes(out), slots, sorted(percs)


def compress(data, maxdepth=8):
    """Tokens: 0nnnnnnn literal run of n+1 bytes; 1lllllll t t s replay
    MINREF+l bytes starting s bytes into the token at compressed offset t."""
    n = len(data)
    tok_at, dep = [None] * n, [0] * n
    out, lit, idx = bytearray(), [], {}

    def flush():
        while lit:
            run = lit[:128]
            del lit[:128]
            start = len(out)
            out.append(len(run) - 1)
            for k, p in enumerate(run):
                tok_at[p], dep[p] = (start, k), 0
                out.append(data[p])
    i = 0
    while i < n:
        best_len, best_src = 0, 0
        if i + 4 <= n:
            for s in reversed(idx.get(data[i:i + 4], [])[-64:]):
                L = 0
                while (i + L < n and s + L < i and L < MAXREF and data[s + L] == data[i + L]
                       and dep[s + L] < maxdepth):
                    L += 1
                if L > best_len:
                    best_len, best_src = L, s
        if best_len >= MINREF:
            flush()
            t, k = tok_at[best_src]
            start = len(out)
            out += bytes([0x80 | (best_len - MINREF), t & 255, t >> 8, k])
            for j in range(best_len):
                tok_at[i + j] = (start, j)
                dep[i + j] = dep[best_src + j] + 1
                if i + j + 4 <= n:
                    idx.setdefault(data[i + j:i + j + 4], []).append(i + j)
            i += best_len
        else:
            lit.append(i)
            if i + 4 <= n:
                idx.setdefault(data[i:i + 4], []).append(i)
            i += 1
    flush()
    return bytes(out), max(dep) + 1


def rows(vals, fmt='$%02X', per=16):
    return '\n'.join('        !byte ' + ', '.join(fmt % v for v in vals[i:i + per])
                     for i in range(0, len(vals), per))


def build(wad, lump):
    lumps = doomopl.wad_lumps(wad)
    if lump not in lumps:
        sys.exit('%s has no %s' % (wad, lump))
    mus = lumps[lump]
    gm = lumps['GENMIDI']
    length, start = struct.unpack('<HH', mus[4:8])
    score, slots, percs = rewrite(mus[start:start + length], None)
    comp, depth = compress(score)
    if depth > 15:
        sys.exit('nesting %d is deeper than the player\'s 16 frames' % depth)

    perc_map = [0] * 47
    for k in percs:
        perc_map[k - 35] = slots.index(128 + k - 35)
    ins = b''.join(gm[8 + 36 * s: 8 + 36 * (s + 1)] for s in slots)
    name = 'DoomPlay_' + lump
    data = ['; generated by doomplay.py from %s %s - do not edit' % (os.path.basename(wad), lump),
            'SCORELEN = %d' % len(score),
            'NUMINS   = %d' % len(slots),
            'INITINS  = %d' % slots.index(0),
            'MINREF   = %d' % MINREF,
            '', 'FreqLo', rows([f & 255 for f in FREQUENCY]),
            'FreqHi', rows([f >> 8 for f in FREQUENCY]),
            'VolMap', rows(VOLUME, '%d'),
            'PercMap', rows(perc_map, '%d'),
            'InsLo', rows(['<(InsData+%d)' % (36 * i) for i in range(len(slots))], '%s'),
            'InsHi', rows(['>(InsData+%d)' % (36 * i) for i in range(len(slots))], '%s'),
            'InsData', rows(list(ins)),
            'Score', rows(list(comp)), 'ScoreEnd', '']
    with open(name + '_data.a', 'w', newline='\n') as f:
        f.write('\n'.join(data))
    with open(name + '.a', 'w', newline='\n') as f:
        f.write('; %s %s\n* = $%04X\n!source "%s"\n!source "%s"\n'
                % (os.path.basename(wad), lump, LOAD,
                   os.path.join(HERE, 'DoomPlay.asm').replace('\\', '/'), name + '_data.a'))
    r = subprocess.run([ACME, '-f', 'plain', '-o', name + '.bin', '--symbollist', name + '.sym',
                        name + '.a'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit('acme failed:\n' + r.stdout.decode('utf-8', 'replace'))
    size = os.path.getsize(name + '.bin')
    end = LOAD + size - 1
    print('%s: score %d bytes -> %d packed (nesting %d), %d instruments'
          % (lump, len(score), len(comp), depth, len(slots)))
    print('wrote %s, $%04X-$%04X (%d bytes)%s' % (name + '.bin', LOAD, end, size,
          '' if end < 0x4000 else '  - PAST THE TOP OF RAM'))
    if end >= 0x4000:
        sys.exit(1)
    return name, mus, gm


def check(name, wad, lump, ticks=4000):
    """Run the binary in py65 with a free-running VIA T1 and compare its
    OPL2 writes, and the tick each one goes out in, with doomopl.py."""
    import re
    from py65.devices.mpu65c02 import MPU
    from py65.memory import ObservableMemory
    sym = open(name + '.sym').read()
    sy = lambda s: int(re.search(r'\b%s\s*=\s*\$([0-9a-fA-F]+)' % s, sym).group(1), 16)
    period = 1000000 // TICK_HZ             # T1N + 2
    mem = ObservableMemory()
    mpu = MPU(memory=mem)
    for i, b in enumerate(open(name + '.bin', 'rb').read()):
        mem[LOAD + i] = b
    via = {'start': None, 'cleared': 0}
    reg, writes = [0], []

    def expiries():
        if via['start'] is None:
            return 0
        return (mpu.processorCycles - via['start']) // period

    def t1ch(a, v):
        via['start'] = mpu.processorCycles
    def ifr(a):
        return 0x40 if expiries() > via['cleared'] else 0
    def t1cl(a):
        via['cleared'] = expiries()
        return 0
    mem.subscribe_to_write([0x6005], t1ch)
    mem.subscribe_to_read([0x600D], ifr)
    mem.subscribe_to_read([0x6004], t1cl)
    mem.subscribe_to_write([0x5420], lambda a, v: reg.__setitem__(0, v))
    mem.subscribe_to_write([0x5430], lambda a, v: writes.append((expiries() - 1, reg[0], v)))
    mem.subscribe_to_read(range(0xD400, 0xD800), lambda a: 0xFE)
    mpu.pc = sy('DOOM_START')
    mpu.sp = 0xFF
    while via['start'] is None or expiries() < ticks + 2:
        mpu.step()

    lumps = doomopl.wad_lumps(wad)
    main_i, perc_i = doomopl.genmidi(lumps['GENMIDI'])
    drv = doomopl.OPLDriver(main_i, perc_i)
    drv.init_registers()
    want = [(-1, r, v) for r, v in drv.writes]
    for t, ws in doomopl.play_mus(lumps[lump], drv):
        if t >= ticks:
            break
        want += [(t, r, v) for r, v in ws]
    got = [w for w in writes if w[0] < ticks]
    late = [g[0] - w[0] for g, w in zip(got, want)]
    for i, (g, w) in enumerate(zip(got, want)):
        if g[1:] != w[1:]:
            print('FIRST DIFFERENCE at write %d: 6502 %02X=%02X, doomopl %02X=%02X'
                  % (i, g[1], g[2], w[1], w[2]))
            return False
    n = min(len(got), len(want))
    print('first %d ticks: %d OPL2 writes, the same as doomopl.py and in order; '
          'on time %d, late %d (worst %d tick%s), early %d'
          % (ticks, n, sum(1 for d in late if d == 0), sum(1 for d in late if d > 0),
             max(late), '' if max(late) == 1 else 's', sum(1 for d in late if d < 0)))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('wad')
    ap.add_argument('lump', nargs='?', default='D_E1M1')
    ap.add_argument('--check', action='store_true', help='compare with doomopl.py in py65')
    args = ap.parse_args()
    name, _, _ = build(args.wad, args.lump)
    if args.check:
        sys.exit(0 if check(name, args.wad, args.lump) else 1)


if __name__ == '__main__':
    main()
