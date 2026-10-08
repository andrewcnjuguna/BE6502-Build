"""A scripted conversation between the local model and a fake WozMon.

    BE6502_TUNES=~/BE6502_SidPlayer LOCAL_LLM_URL=http://<mac>:8080/v1 \\
        python3 tests/try_model.py

Tests the model, not the code: does it look tunes up before playing, pick
the screen-safe build for a picture, stop and ask for reset when a tune is
already playing, and carry on after. Approvals are given automatically and
logged. The model is not deterministic, so this reports rather than
asserts - read the transcript.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fake_wozmon import FakeWozMon  # noqa: E402

woz = FakeWozMon()
os.environ["BE6502_PORT"] = woz.port

from be6502_agent import agent as agent_mod, machine  # noqa: E402

if sys.platform == "darwin":
    machine.be6502.serial.Serial.flush = lambda self: None  # see test_machine.py

calls = []
real_execute = agent_mod.execute_tool


def logged_execute(name, args):
    calls.append((name, args))
    return real_execute(name, args)


agent_mod.execute_tool = logged_execute


def approve(description):
    print(f"\n>>> approval asked: {description}  -> approved")
    return True


bot = agent_mod.BE6502Agent(confirm=approve)


def turn(text):
    print(f"\n=== you> {text}")
    before, ran_before, t0 = len(calls), list(woz.ran), time.time()
    bot.send(text)
    new = calls[before:]
    print(f"--- {time.time() - t0:.0f} s; tools: "
          + (", ".join(f"{n}({a})" for n, a in new) or "none"))
    return new, woz.ran[len(ran_before):]


results = []


def verdict(what, ok):
    results.append((what, ok))
    print(("PASS " if ok else "MISS ") + what)


new, ran = turn("What Paperboy tunes do you have?")
verdict("looked the tunes up instead of answering from nowhere", any(n == "list_tunes" for n, _ in new))
verdict("did not play anything when only asked what there is", not ran)

new, ran = turn("Play the Paperboy one with its picture on the screen.")
plays = [a for n, a in new if n == "play_tune"]
verdict("played a tune", bool(ran))
verdict("chose the screen-safe _low build",
        bool(plays) and "_low" in plays[-1].get("tune", ""))
verdict("sent the Paperboy picture", bool(plays) and "paperboy_screen" in plays[-1].get("picture", ""))

new, ran = turn("Great. Now play Ghostbusters.")
verdict("did not load while Paperboy was still playing", not ran)
verdict("found out the machine was busy (machine_status or a failed play)",
        any(n in ("machine_status", "play_tune") for n, _ in new))
verdict("asked for reset", "reset" in str(bot.messages[-1]["content"]).lower())

woz.reset()
new, ran = turn("OK, I pressed reset.")
verdict("played Ghostbusters after the reset",
        bool(ran) and any("ghostbusters" in a.get("tune", "").lower() for n, a in new if n == "play_tune"))

print(f"\n{sum(ok for _, ok in results)}/{len(results)} as hoped")
