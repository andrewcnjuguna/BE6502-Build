r"""
Standard MIDI file -> Doom MUS score, so doomplay.py can play any MIDI on
the SIDKick pico's OPL2 with Doom's own driver and GENMIDI instruments.

    python3 midi2mus.py ff302.mid                 writes ff302.mus
    python3 doomplay.py DOOM1.WAD ff302.mid       does this itself
    python3 midi2mus.py ff302.mid ff303.mid -o opening.mus
                                                  plays one after the other

Files joined end to end start clean: at each join every channel gets all
notes off, and bend, volume and program go back to MIDI's defaults.

    python3 midi2mus.py ff7_main.mid --seconds 150
                                                  stops at 2:30

A cut fades out over the 3 seconds before it, in 12 volume steps, then
stops every note. Most game MIDIs play their loop twice or more, so a cut
after the first time round loses little.

MUS runs at a fixed 140 ticks a second and has no tempo, so the MIDI's
tempo map is baked into the times: every event lands on the nearest MUS
tick, at most 3.6 ms off. What Doom's driver ignores is dropped - pan,
reverb, sysex, text. What it would get wrong is folded in first:
    expression (CC 11) scales channel volume (CC 7), which the driver obeys;
    pitch bend is rescaled from the MIDI's bend range (RPN 0, 2 semitones
    unless the file sets it) to the driver's fixed +-2 semitones.
MIDI channel 10 becomes MUS percussion channel 15; channels 11-16 move
down one to fill the gap, as in Doom's own mus2mid in reverse.
"""
import argparse
import os
import struct
import sys

TICK_HZ = 140
FADE = 3                                    # seconds of fade before a cut
FADE_STEPS = 12
PERC = 9                                    # MIDI channel 10, counted from 0


def varlen(d, i):
    v = 0
    while True:
        b = d[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def read_midi(data):
    """(division, [(tick, track, seq, status, data bytes)]) - channel events
    and tempo metas (status 0x51) from every track, in time order."""
    if data[:4] != b'MThd':
        sys.exit('not a standard MIDI file')
    hlen, fmt, ntracks, division = struct.unpack('>IHHH', data[4:14])
    if division & 0x8000:
        sys.exit('SMPTE time division is not supported')
    pos = 8 + hlen
    out = []
    for t in range(ntracks):
        while data[pos:pos + 4] != b'MTrk':         # skip unknown chunks
            pos += 8 + struct.unpack('>I', data[pos + 4:pos + 8])[0]
        n = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        tr = data[pos + 8:pos + 8 + n]
        pos += 8 + n
        i, tick, running = 0, 0, 0
        while i < len(tr):
            delta, i = varlen(tr, i)
            tick += delta
            s = tr[i]
            if s == 0xFF:
                kind = tr[i + 1]
                n, i = varlen(tr, i + 2)
                if kind == 0x51:
                    out.append((tick, t, len(out), 0x51, tr[i:i + 3]))
                elif kind == 0x2F:
                    out.append((tick, t, len(out), 0x2F, b''))
                i += n
            elif s in (0xF0, 0xF7):
                n, i = varlen(tr, i + 1)
                i += n
            else:
                if s & 0x80:
                    running = s
                    i += 1
                size = 1 if running & 0xF0 in (0xC0, 0xD0) else 2
                out.append((tick, t, len(out), running, tr[i:i + size]))
                i += size
    out.sort(key=lambda e: (e[0], e[2]))
    return division, out


def to_mus_ticks(division, events):
    """Pair each event with its time in MUS ticks, through the tempo map."""
    tempo, last_tick, secs = 500000, 0, 0.0
    for tick, _, _, status, d in events:
        secs += (tick - last_tick) * tempo / 1e6 / division
        last_tick = tick
        if status == 0x51:
            tempo = int.from_bytes(d, 'big')
        yield round(secs * TICK_HZ), status, d


def duration(data):
    """Seconds one MIDI file, or a list of them played in turn, lasts."""
    files = [data] if isinstance(data, (bytes, bytearray)) else list(data)
    total = 0
    for midi in files:
        division, events = read_midi(midi)
        total += max((t for t, _, _ in to_mus_ticks(division, events)), default=0)
    return total / TICK_HZ


def convert(data, report=None, seconds=None):
    """MUS from one MIDI file, or from a list of them played in turn.
    seconds cuts it there, fading out over the FADE seconds before."""
    files = [data] if isinstance(data, (bytes, bytearray)) else list(data)
    limit = round(seconds * TICK_HZ) if seconds else None
    fade_from = limit - FADE * TICK_HZ if limit else None
    steps = ([fade_from + FADE * TICK_HZ * k // FADE_STEPS for k in range(1, FADE_STEPS)]
             if limit else [])
    base = [100] * 16                        # volume before the fade, per MUS channel
    sent_vol = [None] * 16                   # MUS volume last sent, per MUS channel
    sent_bend = [128] * 16
    sent_prog = [0] * 16
    velocity = [127] * 16                    # as doomopl.play_mus starts
    programs = set()
    groups = []                              # (mus tick, [event bytes])
    end = 0

    def emit(t, ev):
        if groups and groups[-1][0] == t:
            groups[-1][1].append(bytearray(ev))
        else:
            groups.append((t, [bytearray(ev)]))

    def mus_ch(c):
        return 15 if c == PERC else (c if c < PERC else c - 1)

    def set_volume(t, m, v):
        base[m] = v
        if limit and t > fade_from:
            v = v * (limit - t) // (limit - fade_from)
        if v != sent_vol[m]:
            sent_vol[m] = v
            emit(t, (0x40 | m, 3, v))

    def used():
        return sorted({ev[0] & 15 for _, evs in groups for ev in evs})

    def fade_until(t):
        while steps and steps[0] <= t:
            s = steps.pop(0)
            for m in used():
                set_volume(s, m, base[m])

    def join(t):
        for m in used():
            emit(t, (0x30 | m, 11))          # all notes off
            if sent_bend[m] != 128:
                sent_bend[m] = 128
                emit(t, (0x20 | m, 128))
            if sent_vol[m] is not None:
                set_volume(t, m, 100)
            base[m] = 100
            if sent_prog[m] and m != 15:
                sent_prog[m] = 0
                emit(t, (0x40 | m, 0, 0))

    cut = False
    for n, midi in enumerate(files):
        if cut:
            break
        start = end
        if n:
            join(start)
        division, events = read_midi(midi)
        vol = [100] * 16                     # CC 7
        expr = [127] * 16                    # CC 11
        bend_range = [2.0] * 16
        rpn = [(127, 127)] * 16
        for t, status, d in to_mus_ticks(division, events):
            t += start
            if limit and t >= limit:
                cut = True
                break
            fade_until(t)
            end = max(end, t)
            if status in (0x51, 0x2F):
                continue
            kind, c = status & 0xF0, status & 15
            m = mus_ch(c)
            if kind == 0x90 and d[1]:
                key, v = d[0] & 0x7F, d[1] & 0x7F
                if v != velocity[m]:
                    velocity[m] = v
                    emit(t, (0x10 | m, 0x80 | key, v))
                else:
                    emit(t, (0x10 | m, key))
            elif kind in (0x80, 0x90):
                emit(t, (0x00 | m, d[0] & 0x7F))
            elif kind == 0xE0:
                semis = (((d[1] << 7) | d[0]) - 8192) / 8192 * bend_range[c]
                sent_bend[m] = max(0, min(255, round(128 + semis * 64)))
                emit(t, (0x20 | m, sent_bend[m]))
            elif kind == 0xC0:
                programs.add(d[0] & 0x7F)
                sent_prog[m] = d[0] & 0x7F
                emit(t, (0x40 | m, 0, sent_prog[m]))
            elif kind == 0xB0:
                ctl, val = d[0], d[1] & 0x7F
                if ctl == 7:
                    vol[c] = val
                elif ctl == 11:
                    expr[c] = val
                elif ctl == 121:
                    expr[c] = 127
                elif ctl == 101:
                    rpn[c] = (val, rpn[c][1])
                elif ctl == 100:
                    rpn[c] = (rpn[c][0], val)
                elif ctl == 6 and rpn[c] == (0, 0):
                    bend_range[c] = float(val)
                elif ctl == 120:
                    emit(t, (0x30 | m, 10))
                elif ctl == 123:
                    emit(t, (0x30 | m, 11))
                if ctl in (7, 11, 121):
                    set_volume(t, m, (vol[c] * expr[c] + 63) // 127)

    if cut:
        fade_until(limit)
        for m in used():
            emit(limit, (0x30 | m, 11))      # all notes off
        end = limit

    score = bytearray()
    for i, (t, evs) in enumerate(groups):
        evs[-1][0] |= 0x80
        for ev in evs:
            score += ev
        delay = (groups[i + 1][0] if i + 1 < len(groups) else end) - t
        chunks = [delay & 0x7F]
        delay >>= 7
        while delay:
            chunks.append(0x80 | (delay & 0x7F))
            delay >>= 7
        score += bytes(reversed(chunks))
    score.append(0x60)                       # score end
    if len(score) > 0xFFFF:
        sys.exit('score is %d bytes - more than MUS can hold' % len(score))

    channels = {ev[0] & 15 for _, evs in groups for ev in evs}
    instrs = sorted(programs)
    header_len = 16 + 2 * len(instrs)
    mus = (b'MUS\x1a' + struct.pack('<HHHHHH', len(score), header_len,
                                    len(channels - {15}), 0, len(instrs), 0)
           + b''.join(struct.pack('<H', p) for p in instrs) + bytes(score))
    if report:
        notes = sum(1 for _, evs in groups for ev in evs if ev[0] & 0x70 == 0x10)
        report('%d notes on %d channels over %.1f s%s, programs %s -> MUS score %d bytes'
               % (notes, len(channels), end / TICK_HZ, ' (cut, faded)' if cut else '',
                  ' '.join(str(p) for p in instrs) or 'none', len(score)))
    return mus


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('midi', nargs='+', help='one file, or several to play in turn')
    ap.add_argument('-o', '--out', help='output (default <first midi>.mus)')
    ap.add_argument('--seconds', type=float, help='cut it here, with a fade')
    args = ap.parse_args()
    mus = convert([open(m, 'rb').read() for m in args.midi], report=print,
                  seconds=args.seconds)
    out = args.out or os.path.splitext(args.midi[0])[0] + '.mus'
    with open(out, 'wb') as f:
        f.write(mus)
    print('wrote', out)


if __name__ == '__main__':
    main()
