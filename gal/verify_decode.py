"""Walk all 65536 addresses; compare the GAL sum-of-products against
a plain 'is this address in the range' reference model."""

def bits(a):
    return dict(A15=(a >> 15) & 1, A14=(a >> 14) & 1, A13=(a >> 13) & 1,
                A12=(a >> 12) & 1, A11=(a >> 11) & 1, A10=(a >> 10) & 1)

def gal(a):
    b = bits(a); n = lambda k: 1 - b[k]
    sid = (b['A15'] and b['A14'] and n('A13') and b['A12'] and n('A11') and b['A10']) \
       or (n('A15') and b['A14'] and n('A13') and n('A12') and b['A11'] and n('A10'))
    acia = n('A15') and b['A14'] and n('A13') and b['A12'] and n('A11') and n('A10')
    rom = (b['A15'] and n('A14')) or (b['A15'] and b['A13']) or (b['A15'] and n('A12')) \
       or (b['A15'] and b['A11']) or (b['A15'] and n('A10'))
    # outputs are active low: asserted == 0 on the pin
    return (0 if sid else 1), (0 if acia else 1), (0 if rom else 1)

def ref(a):
    sid  = (0x4800 <= a <= 0x4BFF) or (0xD400 <= a <= 0xD7FF)
    acia = 0x5000 <= a <= 0x53FF
    rom  = a >= 0x8000 and not (0xD400 <= a <= 0xD7FF)
    return (0 if sid else 1), (0 if acia else 1), (0 if rom else 1)

bad = [a for a in range(0x10000) if gal(a) != ref(a)]
print('addresses where the GAL equations disagree with the reference: %d' % len(bad))
if bad:
    for a in bad[:8]:
        print('   $%04X  gal=%s ref=%s' % (a, gal(a), ref(a)))
else:
    def span(pred):
        r, s = [], None
        for a in range(0x10000):
            if pred(a):
                if s is None: s = a
            elif s is not None:
                r.append((s, a - 1)); s = None
        if s is not None: r.append((s, 0xFFFF))
        return r
    for nm, i in (('SID /CS', 0), ('ACIA /CS', 1), ('ROM /CE', 2)):
        rs = span(lambda a, i=i: gal(a)[i] == 0)
        print('   %-9s asserted over %s' % (nm, ', '.join('$%04X-$%04X' % r for r in rs)))
    both = [a for a in range(0x10000) if gal(a)[0] == 0 and gal(a)[2] == 0]
    print('   addresses where SID and ROM would drive together: %d' % len(both))
