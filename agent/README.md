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
| `list_tunes` | searches the library: load range, run address, whether the tune is *screen-safe* (ends below `$2000`, so a picture can go with it), and what it plays on |
| `list_pictures` | the 8 KB `*_screen.bin` pictures |
| `play_tune` | **asks you first** (Approve/Decline), then loads the picture and the tune and runs it |
| `read_memory` | up to 256 bytes through WozMon |
| `search_hvsc` | searches the HVSC index (61,000 tunes) by words in the path |
| `fetch_hvsc` | downloads up to 8 tunes with `code/hvsc_fetch.py`: skips what this machine can't play (with the reason), moves too-high tunes to `$1000` with sidreloc, converts with `SidToBE6502.py` |
| `find_cover` | lists a game's pictures on C64-Wiki and Wikipedia - covers and title screens first |
| `make_picture` | downloads one of them and converts it with `code/img2be6502.py`; you get a preview on Telegram |

Loading goes through `code/be6502.py`'s `load_program`, the same code as
the CLI. That covers the 2 ms byte timing, the tail read-back that catches
a dropped byte, the `.asm` origin, `BE6502_START` from the `.sym`, and the
refusal to load a picture a tune would overwrite. The model cannot get
around any of these checks.

When a tune starts, Telegram gets a **now playing** card: title, SID #1
chip, SID #2 (off, its chip and address, or FM for the Doom tracks),
PAL/NTSC, and the play rate. The card comes from the tune's own code, not
from the model. For a converted tune that is the `SKSetup` routine
`SidToBE6502.py` generates, which sets the SKpico before the tune starts.
The older hand-ported tunes don't set the SKpico, so their card says they
play on whatever SKConfig last saved. Titles are the 16-character LCD
strings, so long ones are cut off.

Downloads go into `downloads/` in the tune library (`BE6502_DOWNLOADS` to
change it): `downloads/hvsc/converted/` for tunes, `downloads/pictures/` for
pictures. `list_tunes` and `list_pictures` find them straight away. Only file
names, sizes and URLs from the wiki pages reach the model, never page text.
`make_picture` fetches only from C64-Wiki and Wikimedia. C64-Wiki turns
scripts away from its search and API, so `find_cover` tries likely page
names (`The_Last_Ninja`, `Paperboy`) directly. If it finds nothing, give the
exact title.

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
sudo apt install -y git python3-venv acme        # acme assembles converted tunes
sudo usermod -aG dialout $USER                # serial access; log out and back in
git clone https://github.com/andrewcnjuguna/BE6502-Build.git ~/BE6502-Build
cd ~/BE6502-Build/agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env             # token, chat id, the Mac's address
make -C ~/BE6502-Build/code/sidreloc          # moves too-high HVSC tunes
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
.venv/bin/python tests/try_library.py
```

This has the model fetch a tune from HVSC, make a picture from a title
screen, and explain a tune that can't be played. It downloads for real, into
a temporary folder that is deleted afterwards.

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
- Doom tracks need GAL rev 02. The player switches the SKpico's second SID to FM itself.
- A tune that plays silent is not a load failure; `PORTED_TUNES.md` in the
  SidPlayer repo lists the ones known to be silent.
- If `local_llm.py` changes in the flash agent, copy it over. Keeping the
  two the same is the point of this agent.
