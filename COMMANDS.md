# Commands

Every command for working with the BE6502, the Pi agent and the local
model, in one place. Commands for the Mac are run from `~/code/BE6502-Build`
unless they say otherwise.

## Where things are

| | |
|---|---|
| Mac | `192.168.0.152` (DHCP - if it changes, update `LOCAL_LLM_URL` in the Pi's `.env`) |
| Pi | Pi 5, `andrew@192.168.0.161`, key login from the Mac |
| BE6502 serial | PL2303, 19200 8N1. Mac: `/dev/cu.PL2303G-USBtoUART210`. Pi: `/dev/ttyUSB0` |
| Model server | `~/code/local-llm/serve.sh` on the Mac, port 8080, Qwen3.8 27B 6-bit |
| Telegram bot | @be6502_bot - answers only your chat |
| Pi agent | `~/BE6502-Build` on the Pi, a git checkout. Secrets in `agent/.env`, which is not in git |
| Tune library | `~/code/BE6502_SidPlayer` on the Mac, copied to `~/BE6502_SidPlayer` on the Pi |

The serial adapter is plugged into one machine at a time. Only one program
can use the port at once - quit a `be6502.py term` before loading.

## The BE6502 from the Mac

```bash
python3 code/be6502.py term
```

Opens a WozMon terminal. Ctrl-] quits.

```bash
python3 code/be6502.py load ~/code/BE6502_SidPlayer/C64_games/BE6502_Paperboy_1_low.bin --screen ~/code/BE6502_SidPlayer/C64_games/paperboy_screen.bin --run
```

Loads a picture and a tune, then runs the tune. `--screen` and `--run` are
optional. The load address comes from the `.asm` beside the `.bin`, and the
run address from the `.sym`.

```bash
python3 code/be6502.py load code/FMTest.bin 400 --run
```

Loads a program that has no `.asm` beside it, at `$0400`. Add `--verify` to
read every byte back before running (slow).

By hand in WozMon: `400L` then send the file, `400R` to run, `FFFA.FFFF` to
examine memory, Escape to cancel a line.

A tune loops until **reset**. Nothing gets through on serial until you press
it. RAM survives reset. SKpico settings survive reset but not a power cycle.

## Making tunes and pictures (Mac, in `code/`)

```bash
python3 SidToBE6502.py Commando.sid
```

Converts a `.sid` file. Optional extra arguments: `[song] [rate_hz] [--no-skpico]`.

```bash
python3 hvsc_fetch.py --composer Hubbard_Rob --runnable --convert
```

Fetches tunes this machine can play from HVSC and converts them. Add
`--dry-run` to list without downloading.

```bash
make -C sidreloc
```

Builds sidreloc once. `hvsc_fetch.py` needs it for tunes that load too high.

```bash
python3 img2be6502.py rambo.png
```

Makes `rambo_screen.bin` and a preview PNG. For a portrait picture, use
`--crop X,Y,W,H`.

```bash
python3 doomplay.py DOOM1.WAD D_E1M1
```

Builds `DoomPlay_D_E1M1.bin`. Needs GAL rev 02. `--check` compares every
OPL2 write against the reference in py65.

## Local model server (Mac)

```bash
cd ~/code/local-llm && HOST=0.0.0.0 ./serve.sh
```

Serves the model to the network, so the Pi can reach it. It has no
password: home network only. Ctrl-C stops it. Without `HOST=0.0.0.0` only
the Mac can reach it.

```bash
curl -s http://127.0.0.1:8080/v1/models
```

Checks the server is up.

The bot needs the server running and the Mac awake.

## The Pi agent

### Day to day

When a tune starts, the bot sends a **now playing** card: SID chip,
PAL/NTSC, second SID or FM, play rate.

Telegram commands: `/status` checks the model server and the machine
without involving the model. `/new` starts a fresh conversation. `/help`
lists the commands.

```bash
ssh andrew@192.168.0.161
```

Logs into the Pi.

```bash
ssh andrew@192.168.0.161 'journalctl -u be6502-agent -f'
```

Shows the bot's live log. Ctrl-C stops watching; the bot keeps running.

```bash
ssh andrew@192.168.0.161 'sudo systemctl restart be6502-agent'
```

Restarts the bot. Do this after changing `.env` or the code.

```bash
ssh andrew@192.168.0.161 'sudo systemctl stop be6502-agent'
```

Stops the bot. It starts again at the next boot, unless you also run
`sudo systemctl disable be6502-agent`.

### Updating

```bash
ssh andrew@192.168.0.161 'cd ~/BE6502-Build && git pull --ff-only && sudo systemctl restart be6502-agent'
```

Gets code pushed from the Mac.

```bash
rsync -a --exclude .git --exclude '*.exe' --exclude '*.dll' ~/code/BE6502_SidPlayer/ andrew@192.168.0.161:BE6502_SidPlayer/
```

Copies new tunes to the Pi. It is not a git clone, because `C64_games/`,
the Doom tracks and the `.sym` files are gitignored.

### Settings

```bash
ssh -t andrew@192.168.0.161 'nano ~/BE6502-Build/agent/.env'
```

Edits the Pi's settings: bot token, chat id, model URL, tune folder. Keep
the single quotes: without them the Mac expands `~` to `/Users/andrew` and
nano opens an empty file that cannot be saved. In nano, Ctrl-O then Enter
saves and Ctrl-X exits. Restart the bot afterwards.

To find the chat id, send the bot a message, then open
`https://api.telegram.org/bot<token>/getUpdates` in a browser and read
`message.chat.id`.

### Testing

```bash
ssh andrew@192.168.0.161 'cd ~/BE6502-Build/agent && .venv/bin/python tests/test_machine.py'
```

Tests the tools against a fake WozMon. No machine or model is needed. It
takes about 40 s.

```bash
ssh andrew@192.168.0.161 'cd ~/BE6502-Build/agent && .venv/bin/python tests/try_model.py'
```

Runs a scripted chat between the model and the fake machine. This is the
test of the model itself. Needs the model server running.

```bash
ssh -t andrew@192.168.0.161 'cd ~/BE6502-Build/agent && .venv/bin/python -m be6502_agent'
```

Chats with the agent in a terminal instead of on Telegram.

## When something is wrong

| symptom | try |
|---|---|
| The BE6502 doesn't answer | a tune is playing: press reset. Still nothing: check the power, replug the adapter |
| "port busy" | a `be6502.py term` is open somewhere. Quit it |
| `/status` says the model server is not reachable | start `serve.sh` with `HOST=0.0.0.0`; check the Mac is awake and its address hasn't changed |
| The bot doesn't reply at all | read the log (`journalctl` above). "409 Conflict" means something else is polling the same bot token |
| A tune loads but is silent | it may be a known non-player - see `PORTED_TUNES.md` in the SidPlayer repo. Is the clock 1 MHz? |
| "a byte was dropped" | load again. It keeps happening: add `--verify` |
| ssh asks for a password | the key is missing on the Pi. Run `ssh-copy-id -i ~/.ssh/id_ed25519.pub andrew@192.168.0.161` |
