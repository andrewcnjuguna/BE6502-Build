"""Walk all 65536 addresses; compare the GAL sum-of-products against
a plain 'is this address in the range' reference model."""

def bits(a):
    return dict(A15=(a >> 15) & 1, A14=(a >> 14) & 1, A13=(a >> 13) & 1,
                A12=(a >> 12) & 1, A11=(a >> 11) & 1, A10=(a >> 10) & 1)

def gal(a):
    b = bits(a); n = lambda k: 1 - b[k]
    sid = (b['A15'] and b['A14'] and n('A13') and b['A12'] and n('A11') and b['A10']) \
       or (n('A15') and b['A14'] and n('A13') and n('A12') and b['A11'] and n('A10'))
    lcd = n('A15') and b['A14'] and n('A13') and n('A12') and b['A11'] and b['A10']
    acia = n('A15') and b['A14'] and n('A13') and b['A12'] and n('A11') and n('A10')
    rom = (b['A15'] and n('A14')) or (b['A15'] and b['A13']) or (b['A15'] and n('A12')) \
       or (b['A15'] and b['A11']) or (b['A15'] and n('A10'))
    return tuple(0 if x else 1 for x in (sid, lcd, acia, rom))

def ref(a):
    sid  = (0x4800 <= a <= 0x4BFF) or (0xD400 <= a <= 0xD7FF)
    lcd  = 0x4C00 <= a <= 0x4FFF
    acia = 0x5000 <= a <= 0x53FF
    rom  = a >= 0x8000 and not (0xD400 <= a <= 0xD7FF)
    return tuple(0 if x else 1 for x in (sid, lcd, acia, rom))

NAMES = ('SID /CS', 'LCD /CS', 'ACIA /CS', 'ROM /CE')
bad = [a for a in range(0x10000) if gal(a) != ref(a)]
print('addresses where the GAL equations disagree with the reference: %d' % len(bad))
if bad:
    for a in bad[:8]:
        print('   $%04X  gal=%s ref=%s' % (a, gal(a), ref(a)))
    raise SystemExit(1)

def span(i):
    r, s = [], None
    for a in range(0x10000):
        if gal(a)[i] == 0:
            if s is None: s = a
        elif s is not None:
            r.append((s, a - 1)); s = None
    if s is not None: r.append((s, 0xFFFF))
    return r

for i, nm in enumerate(NAMES):
    print('   %-9s asserted over %s'
          % (nm, ', '.join('$%04X-$%04X' % r for r in span(i))))

clash = [a for a in range(0x10000)
         if sum(1 for x in gal(a) if x == 0) > 1]
print('   addresses selecting more than one device: %d' % len(clash))
