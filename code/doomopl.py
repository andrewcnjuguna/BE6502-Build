# SPDX-License-Identifier: GPL-2.0-or-later
r"""
Doom MUS music -> the OPL2 register writes Doom's sound driver makes.

This file is GPL-2.0-or-later, unlike the rest of the repository: its
voice allocation, instrument loading, volume and frequency logic, and the
frequency_curve and volume_mapping_table data, are a Python port of
Chocolate Doom's src/i_oplmusic.c and src/mus2mid.c,
    Copyright (C) 1993-1996 Id Software, Inc.
    Copyright (C) 2005-2014 Simon Howard
which reproduce the DMX library Doom shipped with. It behaves as Doom
v1.9 on an OPL2: nine voices, double-voice instruments on two of them,
v1.9's rule for stealing a voice.

Reads a music lump (MUS) and the GENMIDI instrument bank, both straight
out of a WAD. Doom's own data is not part of this repository; the
shareware DOOM1.WAD (id Software's doom19s.zip) has both. Music volume
is Doom's slider at maximum.

    python3 doomopl.py DOOM1.WAD D_E1M1 -o e1m1.opl

writes the writes as text, one tick of 1/140 s per line that has any:
    <tick> <reg>=<val> <reg>=<val> ...
for doomplay.py to pack for the BE6502. It also reports how many there
are, which is what decides whether a song fits in RAM.
"""
import argparse
import struct
import sys

TICK_HZ = 140                       # MUS ticks per second

from doomopl_tables import FREQUENCY as FREQUENCY_CURVE, VOLUME as VOLUME_MAPPING

VOICE_OPS = [(0x00, 0x03), (0x01, 0x04), (0x02, 0x05), (0x08, 0x0B), (0x09, 0x0C),
             (0x0A, 0x0D), (0x10, 0x13), (0x11, 0x14), (0x12, 0x15)]
FLAG_FIXED, FLAG_2VOICE = 0x0001, 0x0004


def wad_lumps(path):
    w = open(path, 'rb').read()
    ident, n, off = struct.unpack('<4sII', w[:12])
    if ident not in (b'IWAD', b'PWAD'):
        sys.exit('%s is not a WAD' % path)
    out = {}
    for i in range(n):
        p, sz, name = struct.unpack('<II8s', w[off + 16 * i: off + 16 * i + 16])
        out[name.rstrip(b'\0').decode('latin-1')] = w[p:p + sz]
    return out


class Op:
    def __init__(self, b):
        self.tremolo, self.attack, self.sustain, self.waveform, self.scale, self.level = b


class Instr:
    def __init__(self, b):
        self.flags, self.fine_tuning, self.fixed_note = struct.unpack('<HBB', b[:4])
        self.voices = []
        for v in range(2):
            d = b[4 + 16 * v: 20 + 16 * v]
            self.voices.append({'mod': Op(d[0:6]), 'feedback': d[6], 'car': Op(d[7:13]),
                                'offset': struct.unpack('<h', d[14:16])[0]})


def genmidi(b):
    if b[:8] != b'#OPL_II#':
        sys.exit('GENMIDI lump has the wrong header')
    instrs = [Instr(b[8 + 36 * i: 8 + 36 * (i + 1)]) for i in range(175)]
    return instrs[:128], instrs[128:]


class Voice:
    def __init__(self, i):
        self.index = i
        self.op1, self.op2 = VOICE_OPS[i]
        self.instr = None
        self.instr_voice = 0
        self.channel = None
        self.key = self.note = self.freq = self.note_volume = 0
        self.car_volume = self.mod_volume = 0
        self.priority = 0


class Channel:
    def __init__(self, number, music_volume):
        self.number = number
        self.instr = None
        self.volume_base = 100
        self.volume = min(music_volume, 100)
        self.bend = 0


class OPLDriver:
    def __init__(self, main, perc, music_volume=127):
        self.main, self.perc = main, perc
        self.music_volume = music_volume
        self.voices = [Voice(i) for i in range(9)]
        self.free = list(self.voices)
        self.alloced = []
        self.channels = [Channel(i, music_volume) for i in range(16)]
        for c in self.channels:
            c.instr = main[0]
        self.writes = []

    def w(self, reg, val):
        self.writes.append((reg, val & 0xFF))

    def init_registers(self):
        for r in range(0x40, 0x56):
            self.w(r, 0x3F)
        for r in range(0x60, 0xF6):
            self.w(r, 0x00)
        for r in range(0x01, 0x40):
            self.w(r, 0x00)
        self.w(0x04, 0x60)
        self.w(0x04, 0x80)
        self.w(0x01, 0x20)

    # voices
    def get_free(self):
        if not self.free:
            return None
        v = self.free.pop(0)
        self.alloced.append(v)
        return v

    def key_off(self, v):
        self.w(0xB0 + v.index, v.freq >> 8)

    def release(self, i):
        v = self.alloced[i]
        self.key_off(v)
        v.channel = None
        v.note = 0
        del self.alloced[i]
        self.free.append(v)

    def load_op(self, op, data, max_level):
        level = data.scale | (0x3F if max_level else data.level)
        self.w(0x40 + op, level)
        self.w(0x20 + op, data.tremolo)
        self.w(0x60 + op, data.attack)
        self.w(0x80 + op, data.sustain)
        self.w(0xE0 + op, data.waveform)
        return level

    def set_instrument(self, v, instr, iv):
        if v.instr is instr and v.instr_voice == iv:
            return
        v.instr, v.instr_voice = instr, iv
        d = instr.voices[iv]
        modulating = (d['feedback'] & 1) == 0
        v.car_volume = self.load_op(v.op2, d['car'], True)
        v.mod_volume = self.load_op(v.op1, d['mod'], not modulating)
        self.w(0xC0 + v.index, d['feedback'] | 0x30)
        v.priority = 0x0F - (d['car'].attack >> 4) + 0x0F - (d['car'].sustain & 0x0F)

    def set_volume(self, v, volume):
        v.note_volume = volume
        d = v.instr.voices[v.instr_voice]
        midi_volume = 2 * (VOLUME_MAPPING[v.channel.volume] + 1)
        full = (VOLUME_MAPPING[v.note_volume] * midi_volume) >> 9
        car = 0x3F - full
        if car != (v.car_volume & 0x3F):
            v.car_volume = car | (v.car_volume & 0xC0)
            self.w(0x40 + v.op2, v.car_volume)
            if (d['feedback'] & 1) and d['mod'].level != 0x3F:
                mod = max(d['mod'].level, car) | (v.mod_volume & 0xC0)
                if mod != v.mod_volume:
                    v.mod_volume = mod
                    self.w(0x40 + v.op1, mod | (d['mod'].scale & 0xC0))

    def frequency(self, v):
        note = v.note
        d = v.instr.voices[v.instr_voice]
        if not v.instr.flags & FLAG_FIXED:
            note += d['offset']
        while note < 0:
            note += 12
        while note > 95:
            note -= 12
        idx = 64 + 32 * note + v.channel.bend
        if v.instr_voice != 0:
            idx += v.instr.fine_tuning // 2 - 64
        idx = max(idx, 0)
        if idx < 284:
            return FREQUENCY_CURVE[idx]
        sub, octave = (idx - 284) % 384, min((idx - 284) // 384, 7)
        return FREQUENCY_CURVE[sub + 284] | (octave << 10)

    def update_frequency(self, v):
        f = self.frequency(v)
        if v.freq != f:
            self.w(0xA0 + v.index, f & 0xFF)
            self.w(0xB0 + v.index, (f >> 8) | 0x20)
            v.freq = f

    def voice_key_on(self, ch, instr, iv, note, key, volume):
        v = self.get_free()
        if v is None:
            return
        v.channel, v.key = ch, key
        v.note = instr.fixed_note if instr.flags & FLAG_FIXED else note
        self.set_instrument(v, instr, iv)
        self.set_volume(v, volume)
        v.freq = 0
        self.update_frequency(v)

    def replace_existing(self):                 # Doom v1.9
        r = 0
        for i, v in enumerate(self.alloced):
            if v.instr_voice != 0 or v.channel.number >= self.alloced[r].channel.number:
                r = i
        self.release(r)

    # MIDI-level events, as i_oplmusic.c sees them after mus2mid
    def note_off(self, ch, key):
        i = 0
        while i < len(self.alloced):
            v = self.alloced[i]
            if v.channel is ch and v.key == key:
                self.release(i)
            else:
                i += 1

    def note_on(self, ch, key, volume, percussion):
        if volume <= 0:
            return self.note_off(ch, key)
        note = key
        if percussion:
            if key < 35 or key > 81:
                return
            instr = self.perc[key - 35]
            note = 60
        else:
            instr = ch.instr
        if not self.free:
            self.replace_existing()
        self.voice_key_on(ch, instr, 0, note, key, volume)
        if instr.flags & FLAG_2VOICE:
            self.voice_key_on(ch, instr, 1, note, key, volume)

    def channel_volume(self, ch, volume):
        ch.volume_base = volume
        ch.volume = min(volume, self.music_volume)
        for v in self.voices:
            if v.channel is ch:
                self.set_volume(v, v.note_volume)

    def all_notes_off(self, ch):
        i = 0
        while i < len(self.alloced):
            if self.alloced[i].channel is ch:
                self.release(i)
            else:
                i += 1

    def pitch_bend(self, ch, msb):
        ch.bend = msb - 64
        moved, kept = [], []
        for v in self.alloced:
            if v.channel is ch:
                self.update_frequency(v)
                moved.append(v)
            else:
                kept.append(v)
        self.alloced = kept + moved


def play_mus(mus, drv):
    """Walk a MUS score; yields (tick, writes made at that tick)."""
    if mus[:4] != b'MUS\x1a':
        sys.exit('not a MUS lump')
    length, start = struct.unpack('<HH', mus[4:8])
    pos, tick = start, 0
    velocity = [127] * 16
    midi_of = {}                                # MUS channel -> driver channel
    nxt = [0]

    def channel(c):
        if c == 15:
            return drv.channels[15]             # percussion: MIDI 9, i_oplmusic's 15
        if c not in midi_of:
            # In order of first use, as mus2mid does. mus2mid skips 9, MIDI's
            # percussion; here percussion is 15, so 0-14 are all free. The
            # driver only compares channel numbers, so the order is what
            # counts - and with the skip a 15th melodic channel landed on 15.
            midi_of[c] = nxt[0]
            nxt[0] += 1
        return drv.channels[midi_of[c]]

    while pos < start + length:
        drv.writes = []
        while True:
            b = mus[pos]
            pos += 1
            kind, c, last = (b >> 4) & 7, b & 15, b & 0x80
            ch = channel(c)
            if kind == 0:                           # release note
                drv.note_off(ch, mus[pos] & 0x7F)
                pos += 1
            elif kind == 1:                         # play note
                n = mus[pos]
                pos += 1
                if n & 0x80:
                    velocity[c] = mus[pos] & 0x7F
                    pos += 1
                drv.note_on(ch, n & 0x7F, velocity[c], c == 15)
            elif kind == 2:                         # pitch bend, 0-255
                drv.pitch_bend(ch, mus[pos] >> 1)
                pos += 1
            elif kind == 3:                         # system event
                if mus[pos] in (10, 11):            # all sounds off, all notes off
                    drv.all_notes_off(ch)
                pos += 1
            elif kind == 4:                         # change controller
                ctl, val = mus[pos], min(mus[pos + 1], 127)
                pos += 2
                if ctl == 0:
                    ch.instr = drv.main[val]
                elif ctl == 3:
                    drv.channel_volume(ch, val)
            elif kind == 5:                         # end of measure
                pass
            elif kind == 6:                         # score end
                yield tick, drv.writes
                return
            if last:
                break
        if drv.writes:
            yield tick, drv.writes
        delay = 0
        while True:
            d = mus[pos]
            pos += 1
            delay = (delay << 7) | (d & 0x7F)
            if not d & 0x80:
                break
        tick += delay


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('wad')
    ap.add_argument('lump', help='music lump, e.g. D_E1M1')
    ap.add_argument('-o', '--out', help='text output (default <lump>.opl)')
    args = ap.parse_args()

    lumps = wad_lumps(args.wad)
    if args.lump not in lumps:
        sys.exit('%s has no %s' % (args.wad, args.lump))
    main_i, perc_i = genmidi(lumps['GENMIDI'])
    drv = OPLDriver(main_i, perc_i)
    drv.init_registers()
    init = drv.writes
    events = list(play_mus(lumps[args.lump], drv))

    out = args.out or args.lump.lower() + '.opl'
    with open(out, 'w') as f:
        f.write('0 ' + ' '.join('%02X=%02X' % rv for rv in init) + '\n')
        for t, ws in events:
            f.write('%d ' % t + ' '.join('%02X=%02X' % rv for rv in ws) + '\n')
    n = sum(len(ws) for _, ws in events)
    last = events[-1][0] if events else 0
    print('%s: %d writes over %d ticks (%.1f s), plus %d to initialise'
          % (args.lump, n, last, last / TICK_HZ, len(init)))
    print('wrote', out)


if __name__ == '__main__':
    main()
