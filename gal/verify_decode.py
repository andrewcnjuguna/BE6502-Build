"""Walk all 65536 addresses (x PHI2 x R/W) and compare the GAL
sum-of-products against a plain 'is this address in the range' model.

SIDCS, ACIACS and ROMCE are address-only. LCDWR is additionally
qualified with PHI2 and write, because the 74LS574 needs a clean edge.
"""

def gal(a, phi2, rw):
    A15, A14, A13 = (a >> 15) & 1, (a >> 14) & 1, (a >> 13) & 1
    A12, A11, A10 = (a >> 12) & 1, (a >> 11) & 1, (a >> 10) & 1
    n = lambda x: 1 - x
    sid = (A15 and A14 and n(A13) and A12 and n(A11) and A10) \
       or (n(A15) and A14 and n(A13) and n(A12) and A11 and n(A10))
    acia = n(A15) and A14 and n(A13) and A12 and n(A11) and n(A10)
    lcdwr = n(A15) and A14 and n(A13) and n(A12) and A11 and A10 and phi2 and n(rw)
    rom = (A15 and n(A14)) or (A15 and A13) or (A15 and n(A12)) \
       or (A15 and A11) or (A15 and n(A10))
    return tuple(0 if x else 1 for x in (sid, lcdwr, acia, rom))

def ref(a, phi2, rw):
    sid   = (0x4800 <= a <= 0x4BFF) or (0xD400 <= a <= 0xD7FF)
    lcdwr = (0x4C00 <= a <= 0x4FFF) and phi2 == 1 and rw == 0
    acia  = 0x5000 <= a <= 0x53FF
    rom   = a >= 0x8000 and not (0xD400 <= a <= 0xD7FF)
    return tuple(0 if x else 1 for x in (sid, lcdwr, acia, rom))

NAMES = ('SID /CS', 'LCD /WR', 'ACIA /CS', 'ROM /CE')

bad = [(a, p, r) for a in range(0x10000) for p in (0, 1) for r in (0, 1)
       if gal(a, p, r) != ref(a, p, r)]
print('disagreements with the reference model: %d' % len(bad))
if bad:
    for a, p, r in bad[:8]:
        print('   $%04X phi2=%d rw=%d  gal=%s ref=%s'
              % (a, p, r, gal(a, p, r), ref(a, p, r)))
    raise SystemExit(1)

def span(i, phi2=1, rw=0):
    out, s = [], None
    for a in range(0x10000):
        if gal(a, phi2, rw)[i] == 0:
            if s is None: s = a
        elif s is not None:
            out.append((s, a - 1)); s = None
    if s is not None: out.append((s, 0xFFFF))
    return out

for i, nm in enumerate(NAMES):
    print('   %-9s asserted over %s'
          % (nm, ', '.join('$%04X-$%04X' % r for r in span(i))))

# the latch must never see a strobe on a read, or outside PHI2
stray = [(a, p, r) for a in range(0x10000) for p in (0, 1) for r in (0, 1)
         if gal(a, p, r)[1] == 0 and not (p == 1 and r == 0)]
print('   LCD strobes outside "PHI2 high and writing": %d' % len(stray))

clash = [a for a in range(0x10000)
         if sum(1 for x in gal(a, 1, 0) if x == 0) > 1]
print('   addresses selecting more than one device: %d' % len(clash))
