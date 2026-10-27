"""A scripted conversation: the local model growing the library.

    python3 tests/try_library.py

Real downloads from HVSC, C64-Wiki and Wikipedia, into a temporary tune
folder that is deleted afterwards - the real library is not touched, and
no machine is needed. Needs what the tools need: acme, sidreloc built,
py65 and Pillow. Like try_model.py it reports rather than asserts.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

lib = tempfile.mkdtemp(prefix="be6502-lib-")
os.environ["BE6502_TUNES"] = lib
os.environ["BE6502_DOWNLOADS"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from be6502_agent import agent as agent_mod  # noqa: E402

calls, photos = [], []
real_execute = agent_mod.execute_tool


def logged_execute(name, args):
    calls.append((name, args))
    return real_execute(name, args)


agent_mod.execute_tool = logged_execute
bot = agent_mod.BE6502Agent(confirm=lambda d: False,
                            on_photo=lambda path, caption: photos.append(path))
results = []


def turn(text):
    print(f"\n=== you> {text}")
    before, t0 = len(calls), time.time()
    bot.send(text)
    new = calls[before:]
    print(f"--- {time.time() - t0:.0f} s; tools: " + (", ".join(f"{n}({a})" for n, a in new) or "none"))
    return new


def verdict(what, ok):
    results.append((what, ok))
    print(("PASS " if ok else "MISS ") + what)


new = turn("Get Rob Hubbard's Commando from HVSC.")
fetched = [a for n, a in new if n == "fetch_hvsc"]
verdict("searched HVSC first", any(n == "search_hvsc" for n, _ in new))
verdict("fetched Commando and nothing else",
        len(fetched) == 1 and fetched[0]["paths"] == ["/MUSICIANS/H/Hubbard_Rob/Commando.sid"])
verdict("it is in the library now", any(Path(lib).rglob("BE6502_Commando.bin")))

new = turn("Make a picture for the screen from The Last Ninja's title screen.")
made = [a for n, a in new if n == "make_picture"]
verdict("looked for covers first", any(n == "find_cover" for n, _ in new))
verdict("used the C64-Wiki title screen",
        bool(made) and "The_Last_Ninja_Titel" in made[-1]["url"])
verdict("a preview was sent", bool(photos))

new = turn("Now get Martin Galway's Wizball.")
verdict("tried to fetch it", any(n == "fetch_hvsc" for n, _ in new))
reply = str(bot.messages[-1]["content"]).lower()
verdict("said it can't be played here, without claiming success",
        any(w in reply for w in ("can't", "cannot", "couldn't", "could not", "skipped", "not playable",
                                 "unable", "won't")))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} as hoped")
import shutil  # noqa: E402

shutil.rmtree(lib, ignore_errors=True)
