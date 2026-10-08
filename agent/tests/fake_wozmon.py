"""A WozMon stand-in on a pseudo-terminal, for testing without the machine.

Speaks just enough of this BE6502's monitor for be6502.py: Escape cancels
the line (and echoes a backslash), ADDRLEND starts the Fast Binary Load
("Transfer-", bytes, "-Timeout-" after a quiet second), A.B examines
memory, and AR "runs" a program - after which, like a real tune, it never
answers again until reset() is called.

    python3 tests/fake_wozmon.py      prints a port to point BE6502_PORT at;
                                      type 'r' + Enter to press reset
"""

import os
import pty
import re
import select
import threading
import time
import tty


class FakeWozMon:
    def __init__(self):
        self.master, slave = pty.openpty()
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self.mem = bytearray(0x10000)
        self.running = None  # address of the "program" running, if any
        self.ran = []        # every address run, in order
        threading.Thread(target=self._serve, daemon=True).start()

    def reset(self):
        self.running = None

    def _out(self, text):
        os.write(self.master, text.encode("latin-1"))

    def _serve(self):
        line = ""
        load_at, load_end, last_byte = None, None, 0.0
        while True:
            r, _, _ = select.select([self.master], [], [], 0.1)
            if load_at is not None and not r and time.time() - last_byte > 1.0:
                self._out("-Timeout-\r\n")
                load_at = None
            if not r:
                continue
            data = os.read(self.master, 4096)
            for b in data:
                if self.running is not None:
                    continue  # a tune never reads the ACIA
                if load_at is not None:
                    if load_at < load_end:
                        self.mem[load_at] = b
                        load_at += 1
                    last_byte = time.time()
                    continue
                ch = chr(b)
                if ch == "\x1b":
                    line = ""
                    self._out("\\\r\n")
                elif ch == "\r":
                    self._out("\r\n")
                    load = self._command(line.upper())
                    if load:
                        load_at, load_end = load
                        last_byte = time.time()
                    line = ""
                else:
                    line += ch
                    self._out(ch)

    def _command(self, line):
        m = re.fullmatch(r"([0-9A-F]{1,4})L([0-9A-F]{1,4})", line)
        if m:
            self._out("Transfer-")
            return int(m.group(1), 16), int(m.group(2), 16)
        m = re.fullmatch(r"([0-9A-F]{1,4})\.([0-9A-F]{1,4})", line)
        if m:
            a0, a1 = int(m.group(1), 16), int(m.group(2), 16)
            a = a0
            while a <= a1:
                row = range(a, min(a1, (a | 7)) + 1)
                self._out("%04X:%s\r\n" % (a, "".join(" %02X" % self.mem[x] for x in row)))
                a = row[-1] + 1
            return None
        m = re.fullmatch(r"([0-9A-F]{1,4})R", line)
        if m:
            self.running = int(m.group(1), 16)
            self.ran.append(self.running)
        return None


if __name__ == "__main__":
    woz = FakeWozMon()
    print(f"fake WozMon on {woz.port} - 'r' + Enter presses reset, Ctrl-C quits")
    try:
        while True:
            if input().strip().lower() == "r":
                woz.reset()
                print("reset")
    except (KeyboardInterrupt, EOFError):
        pass
