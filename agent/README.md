# BE6502 agent

Ask for tunes on Telegram and a Raspberry Pi loads them into the BE6502 and
runs them. The model is the local one, `~/code/local-llm/serve.sh` on the
Mac, so nothing goes to a cloud API.

This is a dry run of the flash agent's local-model setup on hardware that
cannot be bricked. It uses the flash agent's loop, the same `local_llm.py`
translation layer and the same Telegram Approve/Decline gate. If Qwen picks
the wrong tool or garbles arguments here, it will do the same driving E-Sys.

```
phone ── Telegram ── Pi ── USB serial ── BE6502
                      │
                      └── LAN ── Mac: mlx_lm.server (Qwen 27B)
```

## What it can do

| tool | |
|---|---|
| `machine_status` | sends Escape and listens. WozMon echoes; a running tune says nothing |
| `list_tunes` | searches the library: load range, run address, and whether the tune is *screen-safe* (ends below `$2000`, so a picture can go with it) |
| `list_pictures` | the 8 KB `*_screen.bin` pictures |
| `play_tune` | **asks you first** (Approve/Decline), then loads the picture and the tune and runs it |
| `read_memory` | up to 256 bytes through WozMon |

Loading goes through `code/be6502.py`'s `load_program`, the same code as
the CLI. That covers the 2 ms byte timing, the tail read-back that catches
a dropped byte, the `.asm` origin, `BE6502_START` from the `.sym`, and the
refusal to load a picture a tune would overwrite. The model cannot get
around any of these checks.

Tunes loop forever and only reset stops them. The agent cannot press reset,
so after each tune it asks you to.

Telegram commands: `/status` checks the model server and the machine
without involving the model. When something fails, it tells you which side
the problem is on. `/new` clears the conversation, `/help` lists the commands.

## Setup

### 1. Mac: serve the model on the LAN

```bash
cd ~/code/local-llm && HOST=0.0.0.0 ./serve.sh
```

`mlx_lm.server` has no authentication, so anyone on the network can use it
while it listens on `0.0.0.0`. That is fine at home; do not do it on a shared
network. macOS may ask once whether to allow incoming connections for
Python: say yes. Keep the Mac awake while you use the agent.

### 2. Telegram: a new bot

Ask @BotFather for `/newbot`. **Do not reuse the flash agent's token**: two
processes polling one bot take each other's messages. Send the new bot a
message, then read your chat id from
`https://api.telegram.org/bot<token>/getUpdates` (`message.chat.id`).

### 3. Pi

Any Pi on the LAN with Python 3.9 or newer (Raspberry Pi OS Bullseye or
later). The agent does no model work, so a Pi 3 is plenty.

```bash
sudo apt install -y git python3-venv
sudo usermod -aG dialout $USER                # serial access; log out and back in
git clone https://github.com/andrewcnjuguna/BE6502-Build.git ~/BE6502-Build
cd ~/BE6502-Build/agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env             # token, chat id, the Mac's address
```

The tune library comes from the Mac. Clone the SidPlayer repo and you miss
`C64_games/`, the Doom tracks and every `.sym` (the run addresses), because
those are gitignored. Copy the whole folder instead:

```bash
rsync -a ~/code/BE6502_SidPlayer/ pi@raspberrypi.local:BE6502_SidPlayer/
```

Plug the BE6502's PL2303 adapter into the Pi. It appears as `/dev/ttyUSB0`.

### 4. Try it in stages

On the Pi, from `~/BE6502-Build/agent`:

```bash
.venv/bin/python tests/test_machine.py
```

This runs the tools against a fake WozMon, with no machine and no model.
It takes about 40 s.

```bash
.venv/bin/python tests/try_model.py
```

This runs a scripted conversation between the local model and the fake
WozMon: look tunes up, play one with its picture, refuse a second load
until reset, then carry on. It prints each tool call and a pass/miss line.
This is the test of the model itself.

```bash
.venv/bin/python -m be6502_agent
```

This is a terminal chat with the real machine.

```bash
.venv/bin/python -m be6502_agent.telegram_bot
```

This is the Telegram bot. To start it at boot, see
[be6502-agent.service](be6502-agent.service).

## Notes

- The machine must be at 1 MHz for the SKpico, the same as for any SID work here.
- Doom tracks need the SKpico's second SID set to FM (SKConfig), and GAL rev 02.
- A tune that plays silent is not a load failure; `PORTED_TUNES.md` in the
  SidPlayer repo lists the ones known to be silent.
- If `local_llm.py` changes in the flash agent, copy it over. Keeping the
  two the same is the point of this agent.
