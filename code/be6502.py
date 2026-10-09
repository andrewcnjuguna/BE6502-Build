r"""
Serial terminal and binary loader for the BE6502 - what TeraTerm does on
the Windows machine, for the Mac.

    python3 be6502.py term                       talk to WozMon
    python3 be6502.py load BE6502_Thrust.bin --run    address from its .asm
    python3 be6502.py load SKConfig.bin 400      load at $0400
    python3 be6502.py load SKConfig.bin 400 --run       ...then run it
    python3 be6502.py load SKConfig.bin 400 --verify    ...read it back first
    python3 be6502.py load BE6502_Rambo_First_Blood_Part_II_1.bin --screen rambo_screen.bin --run
                                                 a picture at $2000 first, then the tune

The load address can be left out when an ACME .asm sits beside the .bin
(every converted tune has one): it is the "* = $xxxx" line. Given one that
disagrees, the load refuses - Thrust loads at $0800, and loaded at $1000
it was silent.

--run starts the program at BE6502_START from the .sym file beside the
.bin, which is where a converted tune's driver begins - NOT the load
address, where the tune data starts and the first byte is often $00, a
BRK that crashes the machine. With no .sym it runs from the load address
(right for SKConfig); --run ADDR gives the address outright.

--screen loads a picture made by img2be6502.py at $2000 before the
program, and refuses up front if the program would land on $2000-$3FFF
and overwrite it.

In the terminal, Ctrl-] quits. The port is opened exclusively: a second
copy - or anything else - gets "port busy" instead of quietly sharing it
and each seeing half the bytes, which looks exactly like a hung machine.

Timing is the same as the TeraTerm setup, and both delays matter:

  - The Fast Binary Load drops bytes sent back to back - the second byte
    of a file went missing at full speed. 1 ms per byte, TeraTerm's
    setting, is marginal from this Mac: a 6 KB load once lost 6 bytes.
    So it sends at 2 ms, and after every load reads back the last 16
    bytes - a dropped byte shifts everything after it, so the tail
    catches any drop - and will not run a load that fails that.
  - WozMon echoes slowly, and typed commands sent at 1 ms per character
    get mangled ("400R" arrived as "400D"). The terminal and the commands
    this script types use 30 ms. Typing by hand is slower than that
    anyway; it matters when pasting.

Needs pyserial:  python3 -m pip install pyserial
19200 8N1, no flow control. The port is found automatically if a single
USB serial adapter is plugged in; otherwise give --port.
"""
import argparse
import glob
import os
import re
import select
import sys
import termios
import time
import tty

try:
    import serial
except ImportError:
    sys.exit("needs pyserial:  python3 -m pip install pyserial")

BAUD = 19200
BYTE_GAP = 0.002        # binary load, per byte - 1 ms dropped bytes now and then
KEY_GAP = 0.03          # anything WozMon has to echo, per character
QUIT = b"\x1d"          # Ctrl-]


def find_port():
    ports = sorted(set(glob.glob("/dev/cu.PL2303*") + glob.glob("/dev/cu.usbserial*")
                       + glob.glob("/dev/cu.usbmodem*")
                       + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))  # Linux, e.g. a Pi
    if len(ports) == 1:
        return ports[0]
    if not ports:
        sys.exit("no USB serial port found - is the adapter plugged in and its driver installed?")
    sys.exit("several serial ports, pick one with --port:\n  " + "\n  ".join(ports))


def read_idle(s, idle=1.5, limit=120):
    """Read until the line has been quiet for `idle` seconds."""
    buf, last, end = b"", time.time(), time.time() + limit
    while time.time() < end and time.time() - last < idle:
        b = s.read(512)
        if b:
            buf += b
            last = time.time()
    return buf


def read_until(s, marker, limit=5):
    buf, end = b"", time.time() + limit
    while time.time() < end and marker not in buf:
        buf += s.read(512)
    return buf


def type_line(s, text):
    for ch in text:
        s.write(ch.encode("latin-1"))
        s.flush()
        time.sleep(KEY_GAP)


def show(b):
    sys.stdout.write(b.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n"))
    sys.stdout.flush()


def terminal(s):
    print("connected to %s at %d - Ctrl-] quits" % (s.port, BAUD))
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            r, _, _ = select.select([fd, s], [], [])
            if s in r:
                # WozMon and the programs here send CR LF, which a raw
                # terminal shows as it is
                os.write(sys.stdout.fileno(), s.read(s.in_waiting or 1))
            if fd in r:
                keys = os.read(fd, 64)
                if QUIT in keys:
                    break
                for k in keys:
                    s.write(bytes([k]))
                    s.flush()
                    time.sleep(KEY_GAP)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print()


def origin(path):
    """The '* = $xxxx' origin from the ACME source beside the .bin, if any."""
    asm = os.path.splitext(path)[0] + ".asm"
    if os.path.exists(asm):
        m = re.search(r"^\*\s*=\s*\$([0-9a-fA-F]+)", open(asm, errors="replace").read(), re.M)
        if m:
            return int(m.group(1), 16)
    return None


def entry_point(path, addr):
    """BE6502_START from the ACME symbol list beside the .bin, if any."""
    sym = os.path.splitext(path)[0] + ".sym"
    if os.path.exists(sym):
        m = re.search(r"^\s*BE6502_START\s*=\s*\$([0-9a-fA-F]+)", open(sym).read(), re.M)
        if m:
            return int(m.group(1), 16), os.path.basename(sym)
    return addr, None


def last_address(path, addr):
    """The last address the program uses: the end of the .bin, or of the
    Doom player's write queue (QEND in its .sym) which runs on past it."""
    last = addr + os.path.getsize(path) - 1
    sym = os.path.splitext(path)[0] + ".sym"
    if os.path.exists(sym):
        m = re.search(r"^\s*QEND\s*=\s*\$([0-9a-fA-F]+)", open(sym).read(), re.M)
        if m:
            last = max(last, int(m.group(1), 16) - 1)
    return last


def read_back(s, a0, a1):
    """Bytes $a0-$a1 from WozMon's examine, as a dict address -> value."""
    type_line(s, "%X.%X\r" % (a0, a1))
    text = read_idle(s, 2, 600).decode("latin-1")
    mem = {}
    for a, d in re.findall(r"([0-9A-F]{4}):((?: [0-9A-F]{2})+)", text):
        for i, x in enumerate(d.split()):
            mem[int(a, 16) + i] = int(x, 16)
    return mem


def load(s, path, addr, verify, run):
    data = open(path, "rb").read()
    end = addr + len(data)
    s.reset_input_buffer()
    type_line(s, "\x1b")                    # cancel any half-typed line
    read_idle(s, 0.5)

    type_line(s, "%XL%04X\r" % (addr, end))
    got = read_until(s, b"Transfer-")
    if b"Transfer-" not in got:
        show(got)
        sys.exit("\nthe loader did not start - is WozMon at its prompt?")
    print("sending %d bytes to $%04X-$%04X ..." % (len(data), addr, end - 1))
    for b in data:
        s.write(bytes([b]))
        s.flush()
        time.sleep(BYTE_GAP)
    if b"-Timeout-" not in read_idle(s, 3, 60):
        sys.exit("the loader never reported its timeout")

    tail = max(addr, end - 16)
    mem = read_back(s, tail, end - 1)
    if any(mem.get(a) != data[a - addr] for a in range(tail, end)):
        sys.exit("loaded, but the last bytes do not match - a byte was dropped on the way.\n"
                 "Not running it; load it again.")
    print("loaded, tail checked")

    if verify:
        print("reading it back through WozMon (slow, about 3 s per 100 bytes) ...")
        mem = read_back(s, addr, end - 1)
        bad = [a for a in range(addr, end) if mem.get(a) != data[a - addr]]
        if bad:
            sys.exit("%d bytes differ, first at $%04X - not running it" % (len(bad), bad[0]))
        print("verified, all %d bytes match" % len(data))

    if run:
        if run == "auto":
            start, sym = entry_point(path, addr)
            print("running at $%04X (%s)" % (start, "BE6502_START in " + sym if sym
                                               else "the load address - no .sym"))
        else:
            start = int(run, 16)
            print("running at $%04X" % start)
        type_line(s, "%XR\r" % start)


def load_program(s, path, addr=None, screen=None, verify=False, run=None):
    """The whole `load` command: work out the address, refuse a picture the
    program would overwrite, load the picture then the program. Also what
    the Pi agent calls. Failures exit with the reason, as the CLI does."""
    org = origin(path)
    if addr is None:
        if org is None:
            sys.exit("no load address given and no .asm beside %s to read it from" % path)
        addr = org
    else:
        addr = int(addr, 16)
        if org is not None and org != addr:
            sys.exit("%s is built for $%04X, not $%04X - leave the address out"
                     % (os.path.basename(path), org, addr))
    if screen:
        last = last_address(path, addr)
        if addr <= 0x3FFF and last >= 0x2000:
            sys.exit("%s occupies $%04X-$%04X, over the screen at $2000-$3FFF - the picture\n"
                     "would be overwritten. Use a build that ends below $2000 (the _low ones)."
                     % (os.path.basename(path), addr, last))
        print("picture %s:" % os.path.basename(screen))
        load(s, screen, 0x2000, False, None)
        print("tune %s:" % os.path.basename(path))
    load(s, path, addr, verify, run)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial device, e.g. /dev/cu.PL2303G-USBtoUART210")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("term", help="interactive terminal")
    lp = sub.add_parser("load", help="send a .bin with the Fast Binary Load")
    lp.add_argument("file")
    lp.add_argument("addr", nargs="?",
                    help="load address in hex, e.g. 400 - optional when a .asm sits beside it")
    lp.add_argument("--verify", action="store_true", help="read it back before going on")
    lp.add_argument("--screen", metavar="PICTURE",
                    help="load this 8 KB picture at $2000 first (from img2be6502.py)")
    lp.add_argument("--run", nargs="?", const="auto", metavar="ADDR",
                    help="run it - at BE6502_START from the .sym, or ADDR - then open the terminal")
    args = ap.parse_args()

    port = args.port or find_port()
    try:
        s = serial.Serial(port, BAUD, timeout=0.05, rtscts=False, dsrdtr=False,
                          xonxoff=False, exclusive=True)
    except serial.SerialException as e:
        sys.exit("%s is busy - is another terminal or be6502.py still open?\n(%s)" % (port, e))
    time.sleep(0.2)
    if args.cmd == "load":
        load_program(s, args.file, args.addr, args.screen, args.verify, args.run)
        if not args.run:
            return 0
    terminal(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
