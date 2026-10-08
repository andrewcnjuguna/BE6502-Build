"""The tools against a fake WozMon - no machine, no model.

    BE6502_TUNES=~/BE6502_SidPlayer python3 tests/test_machine.py

Needs pyserial, and the tune library with the C64_games folder (for a
screen-safe tune and its picture). Takes about half a minute: the loads
go at the real 2 ms per byte.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fake_wozmon import FakeWozMon  # noqa: E402

woz = FakeWozMon()
os.environ["BE6502_PORT"] = woz.port

from be6502_agent import machine, tools  # noqa: E402

if sys.platform == "darwin":
    # tcdrain never returns on a macOS pty; a pty has nothing to drain anyway
    machine.be6502.serial.Serial.flush = lambda self: None

TUNE = "C64_games/BE6502_Paperboy_1_low.bin"
PICTURE = "C64_games/paperboy_screen.bin"


def expect_error(fn, *args, contains):
    try:
        fn(*args)
    except machine.MachineError as exc:
        assert contains in str(exc), f"expected {contains!r} in: {exc}"
        return str(exc)
    raise AssertionError(f"{fn.__name__}{args} should have failed")


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


listing = machine.list_tunes("paperboy")
check("list_tunes finds the Paperboy builds", TUNE in listing and "screen-safe" in listing)
check("list_tunes marks the full build as overlapping",
      "BE6502_Paperboy_1.bin" in listing and "overlaps screen" in listing)
check("list_pictures finds the picture", PICTURE in machine.list_pictures())
check("machine_status: ready", "at its prompt" in machine.status())

err = expect_error(machine.play, "C64_games/BE6502_Paperboy_1.bin", PICTURE, contains="over the screen")
check("a picture with a tune that overwrites it is refused before sending", not woz.ran)
expect_error(machine.play, "BE6502_Nonexistent", contains="no tune")
check("an unknown tune is refused", not woz.ran)

lib = Path(os.environ.get("BE6502_TUNES", Path.home() / "BE6502_SidPlayer"))
out = tools.execute_tool("play_tune", {"tune": TUNE, "picture": PICTURE})
tune_bytes, pic_bytes = (lib / TUNE).read_bytes(), (lib / PICTURE).read_bytes()
addr = int(machine.be6502.origin(str(lib / TUNE)))
entry, _ = machine.be6502.entry_point(str(lib / TUNE), addr)
check("picture landed at $2000", woz.mem[0x2000:0x4000] == pic_bytes)
check("tune landed at its origin", woz.mem[addr:addr + len(tune_bytes)] == tune_bytes)
check(f"ran at BE6502_START ${entry:04X}, not the load address", woz.ran == [entry])
check("play reports the tail check", "tail checked" in out)

check("machine_status: busy while the tune plays", "press reset" in machine.status())
expect_error(machine.play, TUNE, contains="press reset")
check("no second load while a tune plays", woz.ran == [entry])

woz.reset()
check("machine_status: ready after reset", "at its prompt" in machine.status())
dump = machine.examine("2000", "2012")
want = " ".join("%02X" % b for b in pic_bytes[:16])
check("read_memory reads the picture back", dump.splitlines()[0] == "$2000: " + want)
expect_error(machine.examine, "0000", "0200", contains="at most")

card = tools.announce("play_tune", {"tune": TUNE, "picture": PICTURE})
check("the now-playing card has the chip, clock and picture",
      "SID #1 8580" in card and "SID #2 off" in card and "PAL" in card and "paperboy_screen" in card)
check("the play result tells the model the setup too", "SID #1 8580" in out)
rtype = tools.announce("play_tune", {"tune": "C64_games/BE6502_R_Type_Amiga_to_2SID_2SID.bin"})
check("a 2SID tune shows SID #2 and where it sits", "SID #2 8580 at $D420" in rtype)
doom = tools.announce("play_tune", {"tune": "Doom/DoomPlay_D_E1M5.bin"})
check("a Doom track shows FM", "OPL2 FM" in doom and "Suspense" in doom)
old_port = tools.announce("play_tune", {"tune": "BE6502_Gray_Matt_Dominator.bin"})
check("an old port says it leaves the SKpico alone", "as SKConfig last saved" in old_port)
check("list_tunes tags what each tune plays on", "[8580 PAL 1 SID]" in machine.list_tunes("paperboy low"))
print("all passed")
