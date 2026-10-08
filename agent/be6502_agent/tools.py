"""Tool definitions and dispatch, in the flash agent's format (Anthropic
tool schemas; local_llm.py turns them into OpenAI functions)."""

from . import machine

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
            "its load range, the address it runs from, and whether it is "
            "screen-safe (ends below $2000, so it can be played with a picture). "
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
]


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
    raise ValueError(f"unknown tool {name!r}")
