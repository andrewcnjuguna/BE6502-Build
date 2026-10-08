"""Environment-driven configuration for the BE6502 agent.

Same convention as the flash agent: plain environment variables, with
agent/.env as their obvious home (copy .env.example). Real environment
variables win over the file.
"""

import os
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_ROOT.parent


def _load_env_file():
    env_file = AGENT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file()


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# --- Local model ---------------------------------------------------------------
# Any OpenAI-compatible /v1/chat/completions server. On the Pi this is the
# Mac running ~/code/local-llm/serve.sh with HOST=0.0.0.0, e.g.
# http://192.168.0.152:8080/v1
LOCAL_BASE_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8080/v1")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "lmstudio-community/Qwen3.8-27B-MLX-6bit")
LOCAL_TEMPERATURE = float(os.environ.get("LOCAL_LLM_TEMPERATURE", "0.6"))
LOCAL_THINKING = _flag("LOCAL_LLM_THINKING")
LOCAL_TIMEOUT = int(os.environ.get("LOCAL_LLM_TIMEOUT_S", "900"))
MAX_TOKENS = int(os.environ.get("LOCAL_LLM_MAX_TOKENS", "16000"))

# --- The BE6502 ------------------------------------------------------------------
# Serial device; empty = the single USB serial adapter found (be6502.py's
# find_port). The PL2303 shows up as /dev/ttyUSB0 on the Pi.
SERIAL_PORT = os.environ.get("BE6502_PORT", "")

# Folders searched (recursively) for tunes and pictures, separated by ':'.
# A tune is a .bin with an ACME .asm beside it giving its load address; a
# picture is an 8 KB *_screen.bin from img2be6502.py.
TUNE_DIRS = [
    Path(p).expanduser()
    for p in os.environ.get("BE6502_TUNES", str(Path.home() / "BE6502_SidPlayer")).split(os.pathsep)
    if p.strip()
]

# --- Telegram ------------------------------------------------------------------
# A bot of its own (from @BotFather): two processes polling one bot token
# fight over its updates, so do not reuse the flash agent's bot.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
APPROVAL_TIMEOUT = int(os.environ.get("BE6502_AGENT_APPROVAL_TIMEOUT_S", "600"))
