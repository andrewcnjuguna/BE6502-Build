"""Parse the CUPL source, evaluate its equations over all 65536 addresses,
and compare against the same reference model verify_decode.py uses.

The point is to catch a transcription error between the galasm and CUPL
versions of the decoder - they are separate files in separate languages,
and nothing else would notice if they drifted apart.
"""
import io, os, re, sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'BE6502DEC_CUPL.PLD')

text = io.open(SRC, encoding='latin-1').read()
text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)          # strip comments

eqs = {}
for stmt in text.split(';'):
    stmt = stmt.strip()
    if not stmt or stmt.upper().startswith('PIN'):
        continue
    m = re.match(r'^([A-Za-z_]\w*)\s*=\s*(.+)$', stmt, re.S)
    if not m:
        continue
    name, expr = m.group(1), m.group(2)
    if name.upper() in ('NAME', 'PARTNO', 'DATE', 'REVISION', 'DESIGNER',
                        'COMPANY', 'ASSEMBLY', 'LOCATION', 'DEVICE'):
        continue
    py = expr.replace('\n', ' ')
    py = re.sub(r'!\s*([A-Za-z_]\w*)', r'(1-\1)', py)        # !X  -> (1-X)
    py = py.replace('&', '*').replace('#', '+')              # AND -> *, OR -> +
    eqs[name] = py

need = ['SIDCS', 'LCDWR', 'ACIACS', 'ROMCE', 'FMSEL']
missing = [n for n in need if n not in eqs]
if missing:
    sys.exit('could not parse equations for: %s' % ', '.join(missing))
print('parsed %d equations from %s' % (len(eqs), os.path.basename(SRC)))
for n in need:
    print('   %-7s = %s' % (n, ' '.join(eqs[n].split())[:78]))

def cupl(a, phi2=1, rw=0):
    env = {'A15': (a >> 15) & 1, 'A14': (a >> 14) & 1, 'A13': (a >> 13) & 1,
           'A12': (a >> 12) & 1, 'A11': (a >> 11) & 1, 'A10': (a >> 10) & 1,
           'PHI2': phi2, 'RW': rw}
    # a pin reads LOW when its equation is true, because of PIN n = !NAME
    return tuple(0 if eval(eqs[n], {'__builtins__': {}}, env) else 1 for n in need)

def ref(a, phi2=1, rw=0):
    sid  = (0x4800 <= a <= 0x4BFF) or (0xD400 <= a <= 0xD7FF)
    lcd  = (0x4C00 <= a <= 0x4FFF) and phi2 == 1 and rw == 0
    acia = 0x5000 <= a <= 0x53FF
    rom  = a >= 0x8000 and not (0xD400 <= a <= 0xD7FF)
    fm   = 0x5400 <= a <= 0x57FF
    return tuple(0 if x else 1 for x in (sid, lcd, acia, rom, fm))

bad = [(a,p,r) for a in range(0x10000) for p in (0,1) for r in (0,1)
       if cupl(a,p,r) != ref(a,p,r)]
print('\naddresses where the CUPL source disagrees with the reference: %d' % len(bad))
if bad:
    for a,p,r in bad[:8]:
        print('   $%04X phi2=%d rw=%d cupl=%s ref=%s' % (a,p,r,cupl(a,p,r),ref(a,p,r)))
    sys.exit(1)

def span(i):
    r, s = [], None
    for a in range(0x10000):
        if cupl(a)[i] == 0:
            if s is None: s = a
        elif s is not None:
            r.append((s, a - 1)); s = None
    if s is not None: r.append((s, 0xFFFF))
    return r

for i, n in enumerate(need):
    print('   %-7s asserted over %s'
          % (n, ', '.join('$%04X-$%04X' % x for x in span(i))))
clash = [a for a in range(0x10000) if sum(1 for x in cupl(a) if x == 0) > 1]
print('   addresses selecting more than one device: %d' % len(clash))
