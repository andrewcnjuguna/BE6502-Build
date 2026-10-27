"""Tool definitions and dispatch, in the flash agent's format (Anthropic
tool schemas; local_llm.py turns them into OpenAI functions)."""

from __future__ import annotations

from . import library, machine

# Tools the operator approves first. Playing a tune is harmless, but gating
# it puts the flash agent's Approve/Decline path through the local model:
# the model has to name the right tool with the right arguments before a
# person ever sees the request.
GATED_TOOLS = {"play_tune"}

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS = [
    {
        "name": "machine_status",
        "description": (
            "Check whether the BE6502 is at the WozMon prompt and ready to load. "
            "Call this before play_tune, and after the user says they pressed reset."
        ),
        "input_schema": _NO_ARGS,
    },
    {
        "name": "list_tunes",
        "description": (
            "Search the tune library on this Pi. Each line gives the tune's path, "
            "its load range, the address it runs from, whether it is "
            "screen-safe (ends below $2000, so it can be played with a picture), "
            "and in [brackets] what it plays on: SID chip, PAL/NTSC, one or two "
            "SIDs, or OPL2 FM. "
            "query is space-separated words that must all appear in the path, "
            "e.g. 'hubbard monty' or 'doom'. Empty query lists everything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words to match, case-insensitive"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_pictures",
        "description": "List the 8 KB pictures that can be shown on the video card while a tune plays.",
        "input_schema": _NO_ARGS,
    },
    {
        "name": "play_tune",
        "description": (
            "Load a tune over serial and run it. The operator approves this "
            "first. Give the tune exactly as list_tunes printed its path. "
            "Optionally a picture (as list_pictures printed it) to load at $2000 "
            "first - only with a screen-safe tune. Takes from a few seconds to "
            "about a minute for the largest tunes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tune": {"type": "string", "description": "Tune path from list_tunes"},
                "picture": {"type": "string", "description": "Optional picture from list_pictures"},
                "verify": {
                    "type": "boolean",
                    "description": "Read every byte back before running (slow: ~3 s per 100 bytes). "
                                   "Only if the user asks, or a load failed its tail check.",
                },
            },
            "required": ["tune"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Read up to 256 bytes of the BE6502's memory through WozMon, as hex. "
            "Addresses in hex, e.g. start '0400', end '040F'. Needs WozMon at its prompt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "First address, hex"},
                "end": {"type": "string", "description": "Last address, hex (inclusive)"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_hvsc",
        "description": (
            "Search the High Voltage SID Collection's index (61,000 C64 tunes) for "
            "tunes not yet in the library. query is space-separated words that must "
            "all appear in the path - a composer, a game, e.g. 'hubbard commando' or "
            "'last ninja'. Paths look like /MUSICIANS/H/Hubbard_Rob/Commando.sid; "
            "game music is mostly filed under its composer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Words to match, case-insensitive"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "fetch_hvsc",
        "description": (
            "Download tunes from HVSC and convert them to run on the BE6502 - up to 8, "
            "with paths exactly as search_hvsc printed them. Tunes the machine cannot "
            "play are skipped with the reason; ones that load too high are moved to "
            "$1000 first. Converted tunes go straight into the library for play_tune. "
            "A few seconds per tune."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "HVSC paths"},
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_cover",
        "description": (
            "Find pictures of a game on C64-Wiki and Wikipedia: box covers, title "
            "screens, screenshots. Returns each picture's file name and URL (and size, "
            "for Wikipedia), covers and title screens first. Give the game's title."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"game": {"type": "string", "description": "Game title, e.g. 'The Last Ninja'"}},
            "required": ["game"],
            "additionalProperties": False,
        },
    },
    {
        "name": "make_picture",
        "description": (
            "Download one picture find_cover listed and convert it for the BE6502's "
            "100x64 screen. The user is sent a preview automatically. The whole "
            "picture is fitted in; for a tall box cover, fill=true crops it to fill "
            "the screen instead, or crop='X,Y,W,H' (in the image's own pixels, see "
            "its size) picks the part to use."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL exactly as find_cover printed it"},
                "name": {"type": "string", "description": "Short name for the picture, e.g. 'last_ninja'"},
                "crop": {"type": "string", "description": "Optional X,Y,W,H"},
                "fill": {"type": "boolean", "description": "Crop to fill the screen"},
            },
            "required": ["url", "name"],
            "additionalProperties": False,
        },
    },
]


def announce(name: str, args: dict) -> tuple[str, str | None] | None:
    """(text, photo path or None) sent straight to the user after a tool
    succeeds - written by code, so it is reported exactly."""
    if name == "play_tune":
        return machine.now_playing(args["tune"], args.get("picture", "")), None
    if name == "make_picture":
        out, preview = library.picture_files(args["name"])
        return f"🖼 {out.name} - how the BE6502 will show it", str(preview)
    return None


def execute_tool(name: str, args: dict) -> str:
    if name == "machine_status":
        return machine.status()
    if name == "list_tunes":
        return machine.list_tunes(args.get("query", ""))
    if name == "list_pictures":
        return machine.list_pictures()
    if name == "play_tune":
        return machine.play(args["tune"], args.get("picture", ""), bool(args.get("verify", False)))
    if name == "read_memory":
        return machine.examine(args["start"], args["end"])
    if name == "search_hvsc":
        return library.search_hvsc(args["query"])
    if name == "fetch_hvsc":
        return library.fetch_hvsc(list(args["paths"]))
    if name == "find_cover":
        return library.find_cover(args["game"])
    if name == "make_picture":
        return library.make_picture(args["url"], args["name"], args.get("crop", ""),
                                    bool(args.get("fill", False)))
    raise ValueError(f"unknown tool {name!r}")
