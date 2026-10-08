"""The agent loop: the flash agent's hand-written tool loop and approval
gate, run against the local model only.

Kept to the flash agent's shape on purpose - history in Anthropic format,
local_llm.py translating each call, tool errors returned to the model, the
gate blocking on the operator - so a model that behaves here behaves the
same way driving E-Sys.
"""

import json

from . import settings
from .local_llm import LocalClient, to_history
from .tools import GATED_TOOLS, TOOLS, announce, execute_tool

# A turn that is still calling tools after this many rounds is looping;
# hand control back to the user rather than hang the chat.
MAX_TOOL_ROUNDS = 12

SYSTEM_PROMPT = """\
You run music on the user's BE6502, a Ben Eater 6502 breadboard computer, \
from a Raspberry Pi wired to its serial port. The user talks to you over \
Telegram, so keep replies short and plain - no Markdown tables.

The machine:
- W65C02 at 1 MHz, 16 KB RAM at $0000-$3FFF, WozMon in ROM.
- Sound is a SIDKick pico: a C64 SID at $D400, a second SID at $D420, and an \
OPL2 FM chip used by the Doom tracks.
- The video card shows RAM $2000-$3FFF as a 100x64 picture.

How playing works:
- Tunes loop forever. Once one runs, the machine stops listening on serial \
until the user presses its reset button. You cannot stop a tune or press \
reset yourself.
- So: call machine_status before play_tune. If WozMon is not at its prompt, \
ask the user to press reset and tell you when they have. Do not try to load \
anyway.
- Only play tunes list_tunes shows you, by the exact path it printed. Never \
guess a name. If the user's request matches several tunes, pick the most \
likely one and say which; if it matches none, say so and suggest near ones.
- A picture can go with a tune only if the tune is screen-safe - the _low \
builds exist for this. Match pictures to tunes by game name.
- Doom tracks (DoomPlay_*) play on the OPL2; the player switches the SKpico's \
second SID to FM by itself.
- When a tune starts, the user is sent a card saying what it plays on (SID \
chip, PAL/NTSC, second SID, FM). Don't repeat it; answer questions about it.
- play_tune waits for the user to approve it. If they decline, ask what they \
want instead.
- If a load fails, report the reason the tool gave. A dropped byte is worth \
one retry; anything about the port means the user should check the adapter.

Don't make up results: say a tune is running only after play_tune returns \
without an error. You can't hear it - if the user says it is silent, \
believe them; some tunes are known not to play here.
"""


class BE6502Agent:
    def __init__(self, confirm, on_status=print, on_text=None):
        """confirm(description) -> bool asks the operator to approve a gated
        tool call; on_status(text) shows progress; on_text(text) gets each
        reply (None = stream to stdout, the terminal behaviour)."""
        self.client = LocalClient()
        self.messages: list[dict] = []
        self.confirm = confirm
        self.on_status = on_status
        self.on_text = on_text

    @property
    def model_name(self) -> str:
        return settings.LOCAL_MODEL

    def _call_model(self):
        stream = self.on_text is None
        on_delta = (lambda t: print(t, end="", flush=True)) if stream else None
        response = self.client.create(SYSTEM_PROMPT, TOOLS, self.messages, on_delta)
        if stream:
            print()
        else:
            text = "".join(b.text for b in response.content if b.type == "text")
            if text.strip():
                self.on_text(text)
        return response

    def _run_tool(self, block) -> dict:
        result = {"type": "tool_result", "tool_use_id": block.id}
        if getattr(block, "error", None):  # model sent malformed arguments
            result["content"] = block.error
            result["is_error"] = True
            return result
        if block.name in GATED_TOOLS:
            if not self.confirm(f"{block.name}({json.dumps(block.input)})"):
                result["content"] = "Operator declined this action."
                result["is_error"] = True
                return result
        self.on_status(f"  [tool] {block.name} {json.dumps(block.input)}")
        try:
            result["content"] = execute_tool(block.name, dict(block.input))
        except Exception as exc:  # tool errors go back to the model, not the user
            result["content"] = f"Error: {exc}"
            result["is_error"] = True
            return result
        try:
            note = announce(block.name, dict(block.input))
        except Exception as exc:
            note = f"[could not describe it: {exc}]"
        if note:
            self.on_status(note)
        return result

    def send(self, user_message: str):
        """One conversational turn: user text in, agent works until done."""
        self.messages.append({"role": "user", "content": user_message})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._call_model()
            self.messages.append({"role": "assistant", "content": to_history(response.content)})
            if response.stop_reason != "tool_use":
                return
            tool_results = [self._run_tool(b) for b in response.content if b.type == "tool_use"]
            self.messages.append({"role": "user", "content": tool_results})
        # Close the turn so the next user message follows an assistant one.
        self.messages.append({"role": "assistant", "content": [{"type": "text", "text": "(stopped)"}]})
        self.on_status(f"[stopped after {MAX_TOOL_ROUNDS} tool rounds - the model may be looping]")
