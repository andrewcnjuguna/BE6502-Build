"""Telegram front end: python -m be6502_agent.telegram_bot

The flash agent's Telegram interface, cut down. It long-polls getUpdates,
so it needs no webhook or public address - fine on a Pi behind home NAT.
Only messages from TELEGRAM_CHAT_ID are acted on; anything else is logged
and ignored. The play_tune gate is an Approve/Decline button pair, and a
typed yes/no works too.

Commands: /new starts a fresh conversation, /status checks the model
server and the machine without involving the model, /help shows help.
"""

import json
import sys
import time
import urllib.error
import urllib.request

from . import machine, settings
from .agent import BE6502Agent
from .local_llm import LocalClient

POLL_TIMEOUT = 50  # server-side long-poll wait, seconds
MAX_MSG = 4000     # Telegram caps messages at 4096 chars

APPROVE_WORDS = {"y", "yes", "ok", "approve", "approved", "go", "play"}
DECLINE_WORDS = {"n", "no", "decline", "stop", "cancel"}

HELP = (
    "BE6502 agent commands:\n"
    "/new - start a fresh conversation\n"
    "/status - is the model server up, is WozMon at its prompt (no model involved)\n"
    "/help - this message\n\n"
    "Anything else goes to the model, e.g.\n"
    "  what Rob Hubbard tunes do you have?\n"
    "  play Paperboy with its picture\n\n"
    "Loading pauses for your Approve/Decline tap. After a tune, press reset "
    "on the BE6502 before the next one."
)


def _chunks(text: str):
    while len(text) > MAX_MSG:
        cut = text.rfind("\n", 1, MAX_MSG)
        if cut < MAX_MSG // 2:
            cut = MAX_MSG
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    if text:
        yield text


def status_report() -> str:
    problem = LocalClient().ping()
    lines = [f"Model server {settings.LOCAL_BASE_URL}: "
             + ("up" if problem is None else f"NOT reachable ({problem})")]
    try:
        lines.append("BE6502: " + machine.status())
    except Exception as exc:
        lines.append(f"BE6502: {exc}")
    return "\n".join(lines)


class TelegramUI:
    def __init__(self):
        self.token, self.chat_id = settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID
        if not self.token or not self.chat_id:
            raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first "
                             "(easiest: agent/.env, see .env.example).")
        self.offset = 0
        self.queued: list[str] = []  # prompts that arrived mid-turn

    def _api(self, method: str, http_timeout: int = 30, **params):
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            data = json.load(resp)
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    def _poll(self) -> list[dict]:
        """One long-poll; network errors return [] after a pause."""
        try:
            updates = self._api("getUpdates", http_timeout=POLL_TIMEOUT + 15, offset=self.offset,
                                timeout=POLL_TIMEOUT, allowed_updates=["message", "callback_query"])
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                print("[telegram] 409 Conflict: something else is polling this bot "
                      "(a second copy, or the flash agent on the same token?)", file=sys.stderr)
                time.sleep(10)
            else:
                print(f"[telegram] poll failed: {exc}", file=sys.stderr)
                time.sleep(3)
            return []
        except Exception as exc:
            print(f"[telegram] poll failed: {exc}", file=sys.stderr)
            time.sleep(3)
            return []
        for u in updates:
            self.offset = max(self.offset, u["update_id"] + 1)
        return updates

    def _from_operator(self, chat: dict) -> bool:
        if str(chat.get("id")) == str(self.chat_id):
            return True
        print(f"[telegram] ignoring message from unauthorized chat {chat.get('id')}")
        return False

    def say(self, text: str) -> None:
        print(text, flush=True)
        for chunk in _chunks(text.strip()):
            try:
                self._api("sendMessage", chat_id=self.chat_id, text=chunk)
            except Exception as exc:
                print(f"[telegram] send failed: {exc}", file=sys.stderr)

    def confirm(self, description: str) -> bool:
        """Approve/Decline buttons, blocking until answered or timed out
        (= declined). Any text other than yes/no declines and becomes the
        next prompt: someone typing instead of tapping wanted a change."""
        prompt = f"APPROVAL REQUIRED\n{description[:3000]}\n\nRun this?"
        try:
            sent = self._api("sendMessage", chat_id=self.chat_id, text=prompt, reply_markup={
                "inline_keyboard": [[{"text": "✅ Approve", "callback_data": "approve"},
                                     {"text": "❌ Decline", "callback_data": "decline"}]]})
        except Exception as exc:
            print(f"[telegram] could not send approval request: {exc}", file=sys.stderr)
            return False

        print(f">>> APPROVAL REQUIRED (waiting on Telegram): {description}")
        deadline = time.time() + settings.APPROVAL_TIMEOUT
        decision = None
        while decision is None and time.time() < deadline:
            for update in self._poll():
                cb = update.get("callback_query")
                if cb:
                    try:
                        self._api("answerCallbackQuery", callback_query_id=cb["id"])
                    except Exception:
                        pass
                    if (decision is None
                            and self._from_operator(cb.get("message", {}).get("chat", {}))
                            and cb.get("data") in ("approve", "decline")):
                        decision = cb["data"] == "approve"
                    continue
                msg = update.get("message")
                if not msg or "text" not in msg or not self._from_operator(msg["chat"]):
                    continue
                text = msg["text"].strip()
                if decision is not None:
                    self.queued.append(text)
                elif text.lower() in APPROVE_WORDS:
                    decision = True
                elif text.lower() in DECLINE_WORDS:
                    decision = False
                else:
                    decision = False
                    self.queued.append(text)
                    self.say("Not loaded - treating your message as a new instruction.")

        verdict = ("APPROVED" if decision else
                   "DECLINED" if decision is not None else "TIMED OUT - declined")
        try:  # freeze the buttons so a stale tap does nothing
            self._api("editMessageText", chat_id=self.chat_id, message_id=sent["message_id"],
                      text=f"{prompt}\n\n[{verdict}]")
        except Exception:
            pass
        print(f">>> {verdict}")
        return bool(decision)

    def next_prompt(self) -> str:
        while True:
            if self.queued:
                return self.queued.pop(0)
            for update in self._poll():
                cb = update.get("callback_query")
                if cb:  # a stale button
                    try:
                        self._api("answerCallbackQuery", callback_query_id=cb["id"],
                                  text="Nothing is waiting for approval.")
                    except Exception:
                        pass
                    continue
                msg = update.get("message")
                if not msg or not self._from_operator(msg["chat"]):
                    continue
                text = (msg.get("text") or "").strip()
                if text:
                    self.queued.append(text)
                else:
                    self.say("I can only read text messages.")


def main():
    ui = TelegramUI()
    agent = BE6502Agent(confirm=ui.confirm, on_status=ui.say, on_text=ui.say)
    ui.say(f"BE6502 agent online ({agent.model_name}).\n{status_report()}\n\n/help for commands.")
    while True:
        try:
            text = ui.next_prompt()
        except KeyboardInterrupt:
            print()
            break
        print(f"you> {text}")
        low = text.lower()
        if low in ("/start", "/help", "help"):
            ui.say(HELP)
        elif low in ("/new", "/reset"):
            agent.messages.clear()
            ui.say("Conversation cleared.")
        elif low == "/status":
            ui.say(status_report())
        else:
            try:
                agent.send(text)
            except Exception as exc:
                ui.say(f"Error: {exc}\n(conversation kept - just ask again)")


if __name__ == "__main__":
    main()
