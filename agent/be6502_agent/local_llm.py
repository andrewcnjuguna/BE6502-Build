"""Local model backend: any OpenAI-compatible /v1/chat/completions server
(mlx_lm.server, LM Studio, llama-server, vLLM, Ollama).

Copied from the flash agent (esys_automation/agent/flash_agent/
local_llm.py, as of db69d86) - exercising this exact translation layer
against the local model is the point of this agent, so keep the two in
step. The agent keeps its history in Anthropic Messages format; this
module translates to OpenAI format on the way out and back into
Anthropic-shaped blocks on the way in.
"""

from __future__ import annotations  # the only change: Python 3.9 (Pi OS Bullseye)

import json
import re
import urllib.request
import uuid
from types import SimpleNamespace

from . import settings

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _get(block, key, default=None):
    """Read a field from a dict block or an SDK/SimpleNamespace block."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(_get(c, "text", "") for c in content or [])


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "user":
            texts = []
            for block in content:
                if _get(block, "type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": _get(block, "tool_use_id"),
                        "content": _tool_result_text(_get(block, "content")),
                    })
                elif _get(block, "type") == "text":
                    texts.append(_get(block, "text"))
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
        else:
            text = "".join(_get(b, "text", "") for b in content if _get(b, "type") == "text")
            calls = [
                {
                    "id": _get(b, "id"),
                    "type": "function",
                    "function": {"name": _get(b, "name"), "arguments": json.dumps(_get(b, "input"))},
                }
                for b in content
                if _get(b, "type") == "tool_use"
            ]
            entry = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
        }
        for t in tools
    ]


def to_history(blocks) -> list[dict]:
    """Plain Anthropic-format dicts for the conversation history, so the
    same history can be replayed to Claude after a /model switch."""
    out = []
    for b in blocks:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


def _parse_args(raw: str) -> tuple[dict, str | None]:
    """(arguments, error). Malformed JSON becomes an error the agent returns
    to the model as a tool_result, so it can correct itself instead of the
    turn crashing."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}, f"Error: invalid JSON in tool arguments: {raw!r}"
    if not isinstance(args, dict):
        return {}, f"Error: tool arguments must be a JSON object, got {raw!r}"
    return args, None


class LocalClient:
    def __init__(self):
        self.base_url = settings.LOCAL_BASE_URL.rstrip("/")
        self.url = self.base_url + "/chat/completions"

    def ping(self, timeout: float = 5) -> str | None:
        """None if the server answers, otherwise the reason it doesn't."""
        try:
            with urllib.request.urlopen(self.base_url + "/models", timeout=timeout):
                return None
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    def create(self, system, tools, messages, on_delta=None):
        """One model call. Returns an object shaped like an Anthropic Message
        (.content blocks of type text/tool_use, .stop_reason)."""
        body = {
            "model": settings.LOCAL_MODEL,
            "messages": to_openai_messages(system, messages),
            "tools": to_openai_tools(tools),
            "max_tokens": settings.MAX_TOKENS,
            "temperature": settings.LOCAL_TEMPERATURE,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": settings.LOCAL_THINKING},
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )

        text, calls, finish = [], {}, None
        with urllib.request.urlopen(req, timeout=settings.LOCAL_TIMEOUT) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if not chunk.get("choices"):
                    continue
                choice = chunk["choices"][0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                    if on_delta:
                        on_delta(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"id": None, "name": "", "args": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["args"] += fn.get("arguments") or ""
                finish = choice.get("finish_reason") or finish

        content = []
        # Some servers leave the reasoning inline; it is not part of the reply.
        reply = _THINK_RE.sub("", "".join(text)).strip()
        if reply:
            content.append(SimpleNamespace(type="text", text=reply))
        for slot in calls.values():
            args, error = _parse_args(slot["args"])
            content.append(SimpleNamespace(
                type="tool_use",
                id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                name=slot["name"],
                input=args,
                error=error,
            ))
        has_tools = any(b.type == "tool_use" for b in content)
        stop = "tool_use" if has_tools else ("max_tokens" if finish == "length" else "end_turn")
        return SimpleNamespace(content=content, stop_reason=stop)
