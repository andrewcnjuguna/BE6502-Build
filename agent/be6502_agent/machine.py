"""The BE6502 over serial, through code/be6502.py.

Everything that touches the wire goes through be6502.py's own functions, so
the agent loads with exactly the CLI's timing (2 ms per byte, 30 ms per
typed character) and the CLI's safety checks: the tail read-back that
catches a dropped byte, the .asm origin check, and the refusal to load a
picture a tune would overwrite. be6502.py reports failure with sys.exit
and progress with print; both are turned into text for the model here.

The port is opened per operation and closed after, exclusively - so a
be6502.py terminal left open on the Pi shows up as "port busy" instead of
two readers each seeing half the bytes.
"""

import contextlib
import io
import re
import sys
from pathlib import Path

from . import settings

sys.path.insert(0, str(settings.REPO_ROOT / "code"))
import be6502  # noqa: E402  (needs pyserial)

SCREEN = (0x2000, 0x3FFF)
SCREEN_BYTES = 0x2000
MAX_EXAMINE = 256  # WozMon prints ~3 s per 100 bytes at 19200


class MachineError(Exception):
    pass


@contextlib.contextmanager
def _captured():
    """Collect be6502.py's prints; turn its sys.exit into MachineError."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            yield out
    except SystemExit as exc:
        text = (out.getvalue() + "\n" + str(exc.code or "")).strip()
        raise MachineError(text) from None


@contextlib.contextmanager
def _port():
    with _captured():
        port = settings.SERIAL_PORT or be6502.find_port()
    try:
        s = be6502.serial.Serial(port, be6502.BAUD, timeout=0.05, rtscts=False,
                                 dsrdtr=False, xonxoff=False, exclusive=True)
    except be6502.serial.SerialException as exc:
        raise MachineError(f"{port} is busy or gone - is a be6502.py terminal open, "
                           f"or did the adapter drop off USB? ({exc})") from None
    try:
        be6502.time.sleep(0.2)
        yield s
    finally:
        s.close()


def _wozmon_answers(s) -> bool:
    """Send Escape and listen. WozMon echoes a backslash and a new line;
    a running tune never reads the ACIA, so it says nothing at all."""
    s.reset_input_buffer()
    be6502.type_line(s, "\x1b")
    return bool(be6502.read_idle(s, 0.8, 3).strip())


NOT_AT_PROMPT = (
    "The BE6502 did not answer on serial, so WozMon is not at its prompt. "
    "Almost always a tune is still playing - they loop forever and only reset "
    "stops them. Otherwise the machine is off or the adapter dropped off USB. "
    "Ask the user to press reset on the BE6502 (and replug the adapter if "
    "that does not help), then check again."
)


def status() -> str:
    with _port() as s:
        if _wozmon_answers(s):
            return f"WozMon is at its prompt on {s.port}. Ready to load."
        return NOT_AT_PROMPT


# --- the library ---------------------------------------------------------------

def _rel(path: Path) -> str:
    for root in settings.TUNE_DIRS:
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


def _scan():
    tunes, pictures = [], []
    for root in settings.TUNE_DIRS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.bin")):
            if path.name.endswith("_screen.bin"):
                if path.stat().st_size == SCREEN_BYTES:
                    pictures.append(path)
                continue
            addr = be6502.origin(str(path))
            if addr is not None:
                tunes.append((path, addr))
    return tunes, pictures


def _describe(path: Path, addr: int) -> str:
    last = be6502.last_address(str(path), addr)
    entry, sym = be6502.entry_point(str(path), addr)
    run = f"run ${entry:04X}" if sym else f"run ${entry:04X} (no .sym - the load address)"
    clear = "screen-safe" if last < SCREEN[0] or addr > SCREEN[1] else "overlaps screen"
    return f"{_rel(path)}  ${addr:04X}-${last:04X}  {run}  {clear}  [{sound(path)['tag']}]"


# --- what a tune plays on --------------------------------------------------------
# Read from the program's own source, not from anything the model says.
# SidToBE6502.py's driver starts with SKSetup, which stores the tune's
# settings into the SKpico's config bytes (RAM only): byte 0 SID #1 chip,
# 8 SID #2 chip, 10 SID #2 address, 59 clock. DoomPlay.asm's SKFM sets
# SID #2 to FM instead. PortSidToBE6502.py's older ports set nothing.

SK_CHIP = {0: "6581", 1: "8580", 2: "8580 + digiboost", 3: "off", 4: "FM (OPL2)"}
SK_SID2_AT = {1: "$D420", 2: "$D500", 3: "$D520", 5: "the IO pad"}
SK_CLOCK = {0: "PAL", 1: "NTSC"}

# The shareware episode's tracks, by Bobby Prince
DOOM_TRACKS = {
    "D_E1M1": "At Doom's Gate", "D_E1M2": "The Imp's Song", "D_E1M3": "Dark Halls",
    "D_E1M4": "Kitchen Ace (And Taking Names)", "D_E1M5": "Suspense",
    "D_E1M6": "On the Hunt", "D_E1M7": "Demons on the Prey", "D_E1M8": "Sign of Evil",
    "D_E1M9": "Hiding the Secrets",
}


# Name prefixes of MIDI builds, as the library names them
MIDI_GAMES = {"FF6_": "Final Fantasy VI", "FF7_": "Final Fantasy VII"}


def _sk_writes(asm: str) -> dict:
    """{config byte: value} that SKSetup stores; value None = copied from
    SID #1's byte (SID #2 'the same chip')."""
    block = asm.partition("\nSKSetup")[2].partition("\nSKLapse")[0]
    writes, last = {}, None
    for line in block.splitlines():
        code = line.split(";")[0].split()
        if code[:1] == ["lda"] and len(code) > 1:
            m = re.fullmatch(r"#\$?(\d+)", code[1])
            last = int(m.group(1)) if m else None
        elif code[:1] == ["sta"] and len(code) > 1:
            m = re.fullmatch(r"SKBuf\+(\d+)", code[1])
            if m:
                writes[int(m.group(1))] = last
    return writes


def sound(path: Path) -> dict:
    """What the tune plays on: {'tag': short, 'lines': [for the card], 'title': ...}."""
    asm_path = path.with_suffix(".asm")
    asm = asm_path.read_text(errors="replace") if asm_path.exists() else ""
    rate = re.search(r"(\d+(?:\.\d+)?) ?Hz", asm[:600])
    rate = f"{rate.group(1)} Hz" if rate else None

    if "DoomPlay.asm" in asm:
        # doomplay.py heads the .asm with "; <wad> <what it plays>": a Doom
        # lump (D_E1M1) or the name it gave a MIDI build (FF7_Cosmo_Canyon).
        head = re.match(r"; \S+ (\S+)", asm)
        source = head.group(1) if head else path.stem.removeprefix("DoomPlay_")
        sound_line = "Sound: OPL2 FM at $5420/$5430 - the SKpico's SID #2 switched to FM"
        if re.fullmatch(r"D_\w+", source):
            name = DOOM_TRACKS.get(source)
            return {
                "tag": "OPL2 FM",
                "title": f"Doom {source[2:]}" + (f" \"{name}\" - Bobby Prince" if name else ""),
                "lines": [sound_line, "Driver: Doom's v1.9 OPL driver on the 6502, 140 Hz MUS ticks"],
            }
        low = source.endswith("_low")
        title = source.removesuffix("_low")
        for prefix, game in MIDI_GAMES.items():
            if title.startswith(prefix):
                title = game + ": " + title.removeprefix(prefix)
        lines = [sound_line,
                 "Driver: a MIDI file through Doom's v1.9 OPL driver and instruments, 140 Hz"]
        if low:
            lines.append("Shortened, with a fade, to leave room for a picture")
        return {"tag": "OPL2 FM, MIDI", "title": title.replace("_", " "), "lines": lines}

    title = re.search(r'^TitleStr\s+!text "(.*)"', asm, re.M)
    author = re.search(r'^AuthorStr\s+!text "(.*)"', asm, re.M)
    title = " - ".join(s.group(1).strip() for s in (title, author) if s and s.group(1).strip())
    title = title or path.stem.removeprefix("BE6502_").replace("_", " ")

    if "\nSKSetup" not in asm:
        return {"tag": "SID, SKpico as saved", "title": title, "lines": [
            "Sound: SID - the tune leaves the SKpico as SKConfig last saved it",
            f"Driver: {rate + ' ' if rate else ''}polled VIA T1"]}

    w = _sk_writes(asm)
    sid1 = SK_CHIP.get(w.get(0), "as saved (the tune doesn't say)")
    if 0 in w and w[0] == 1 and "cmp #2" in asm.partition("\nSKSetup")[2][:2000]:
        sid1 = "8580"  # an 8580 tune keeps digiboost if it was already on
    sid2_type = w.get(8, 3)
    sid2 = "same chip as SID #1" if 8 in w and sid2_type is None else SK_CHIP.get(sid2_type, "unchanged")
    clock = SK_CLOCK.get(w.get(59), "clock as saved")
    if sid2 == "off":
        two = "1 SID"
        sid2_text = "SID #2 off"
    else:
        two = "2SID"
        sid2_text = f"SID #2 {sid2} at {SK_SID2_AT.get(w.get(10), 'its saved address')}"
    return {
        "tag": f"{sid1.split(' (')[0]} {clock} {two}".replace("as saved PAL", "chip as saved, PAL"),
        "title": title,
        "lines": [f"SKpico: SID #1 {sid1}, {sid2_text}, {clock}",
                  f"Driver: {rate or '?'} polled VIA T1" + (", title on the LCD" if "LCDInit" in asm else "")],
    }


def now_playing(tune: str, picture: str = "") -> str:
    """The card sent to Telegram when a tune starts."""
    tunes, pictures = _scan()
    path = _resolve(tune, [p for p, _ in tunes], "tune")
    info = sound(path)
    lines = [f"▶ Now playing: {info['title']}", _rel(path)] + info["lines"]
    if picture:
        lines.append(f"Picture: {_resolve(picture, pictures, 'picture').name}")
    return "\n".join(lines)


def list_tunes(query: str = "", limit: int = 60) -> str:
    tunes, _ = _scan()
    if not tunes:
        dirs = ", ".join(str(d) for d in settings.TUNE_DIRS)
        return f"No tunes found under {dirs} (BE6502_TUNES)."
    words = query.lower().replace("_", " ").split()
    hits = [t for t in tunes
            if all(w in _rel(t[0]).lower().replace("_", " ") for w in words)]
    lines = [_describe(p, a) for p, a in hits[:limit]]
    head = f"{len(hits)} of {len(tunes)} tunes match {query!r}" if words else f"{len(tunes)} tunes"
    if len(hits) > limit:
        head += f" (first {limit} shown - narrow the query)"
    return "\n".join([head] + lines)


def list_pictures() -> str:
    _, pictures = _scan()
    if not pictures:
        return "No pictures (*_screen.bin, 8 KB) found."
    return "\n".join(_rel(p) for p in pictures)


def _resolve(name: str, pool: list[Path], what: str) -> Path:
    """A path relative to a tune folder, or a file name / stem that is unique."""
    want = name.strip().lower().removesuffix(".bin")
    exact = [p for p in pool if _rel(p).lower().removesuffix(".bin") == want]
    if exact:
        return exact[0]
    stem = [p for p in pool if p.stem.lower() == Path(want).name]
    if len(stem) == 1:
        return stem[0]
    if len(stem) > 1:
        raise MachineError(f"several {what}s are called {name!r}: "
                           + ", ".join(_rel(p) for p in stem) + " - give the path")
    raise MachineError(f"no {what} {name!r} - look it up with the list tool first")


def _hex(text: str) -> int:
    m = re.fullmatch(r"\$?([0-9a-fA-F]{1,4})", str(text).strip())
    if not m:
        raise MachineError(f"not a hex address: {text!r}")
    return int(m.group(1), 16)


# --- loading -------------------------------------------------------------------

def play(tune: str, picture: str = "", verify: bool = False) -> str:
    tunes, pictures = _scan()
    path = _resolve(tune, [p for p, _ in tunes], "tune")
    screen = _resolve(picture, pictures, "picture") if picture else None
    with _port() as s:
        if not _wozmon_answers(s):
            raise MachineError(NOT_AT_PROMPT)
        with _captured() as out:
            be6502.load_program(s, str(path), screen=str(screen) if screen else None,
                                verify=verify, run="auto")
    return (out.getvalue().strip() + "\n" + "\n".join(sound(path)["lines"])
            + "\n(The user has been sent this setup already.)"
            + "\nIt is running now. The machine will not answer serial again until "
              "the user presses reset.")


def examine(start: str, end: str) -> str:
    a0, a1 = _hex(start), _hex(end)
    if a0 > a1:
        raise MachineError("start must not be after end")
    if a1 - a0 + 1 > MAX_EXAMINE:
        raise MachineError(f"at most {MAX_EXAMINE} bytes at a time")
    with _port() as s:
        if not _wozmon_answers(s):
            raise MachineError(NOT_AT_PROMPT)
        mem = be6502.read_back(s, a0, a1)
    if not mem:
        raise MachineError("WozMon answered but printed no memory")
    rows = []
    for row in range(a0 & ~0xF, a1 + 1, 16):
        cells = [f"{mem[a]:02X}" if a in mem else ".." for a in range(row, row + 16)
                 if a0 <= a <= a1]
        rows.append(f"${max(row, a0):04X}: " + " ".join(cells))
    return "\n".join(rows)

