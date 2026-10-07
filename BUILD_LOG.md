# Build log

Hardware changes to the machine, newest first. Software changes live in the
project repos, not here.

---

## 2026-10-07 — Doom's E1M1 on the OPL2, and three wiring faults

At Doom's Gate plays on the SKpico's OPL2, with Doom v1.9's own music
driver logic running on the 6502 (`code/DoomPlay.asm`, built by
`doomplay.py` from the shareware WAD). The software is in README; getting
there turned up three faults on the machine.

**ACIA chip selects**

The W65C51 answered across all of `$5000-$5FFF`, so every FM write to
`$5430` also went out of the serial port as junk. The 2026-08-31 entry
says CS1B moved to the GAL and CS0 went to +5V; in fact both were still
on A12 and A13. Now **pin 2 (CS0) → +5V, pin 3 (CS1B) → GAL pin 14 only**,
and the ACIA answers at `$5000-$53FF` alone. Found by reading its status
register (`$10`) back from every 1K slot, after decoding the programmed
`.jed` showed the GAL itself was right.

**The FM select wire**

Re-wiring the ACIA moved the GAL pin 16 → A8/IO wire to pin 17, then 15.
Diagnosed with the scope on the SKpico's pads while `FMHold` ran: the
A8/IO pad must drop for one cycle while the A5/A6 pad is high. Back on
16, it does.

**FM is on the other channel**

The speaker came off the line-out during the work and went back on one
channel only. The SID was audible, FM silent: FM plays as SID #2, which
the panning setting puts on the other side of the stereo line-out. On
both channels, both are heard.

Also learned: v0.30 firmware does not return a fake OPL status on reads
of `$5420`; it leaves the bus floating. And it queues FM writes in a
256-entry ring drained in real time - writes faster than about one per
20 us overflow it and are all lost, so the player spaces them to ~40 us.

---

## 2026-10-06 — FM: the SKpico's OPL2 at `$5420`

The SIDKick pico also emulates a YM3812 (OPL2), presented as the C64's SFX
Sound Expander in place of its second SID. Wired up with one GAL output and
one wire.

- **GAL rev 02.** New output on pin 16, `/FMSEL`, low across the free
  `$5400-$57FF` slot. Programmed from `BE6502DEC_CUPL.PLD` rev 02.
- **Wire:** GAL pin 16 → SKpico **A8/IO** pad. The A5/A6 pad stays on CPU A5,
  which the OPL2 decode also needs high, so the ports land at **`$5420`**
  (register address) and **`$5430`** (data).
- **Config**, RAM only, with SKConfig: SID #2 type 4 (FM), address 5 (IO
  pad). FM and a second SID are alternatives; config byte 8 picks one.
- `$D500` second-SID tunes are now out of reach: the A8/IO pad carries FM
  select instead of CPU A8.

**Verified**

- `FMTest.asm` played the AdLib guide's test note for two seconds.
- The first attempt was silent: the wire was on the wrong pad.
- Reads of `$5420` return the floating bus (`0D`), not the fake OPL status
  the firmware source suggests for type 4. Playback does not need it.

---

## 2026-10-06 — A5 to the SKpico: a second SID at `$D420`

One wire: CPU **A5** to the SIDKick pico's **A5 pad**. That lets the SKpico
tell `$D420` from `$D400`, so with SID #2 switched on (config byte 8 = chip,
byte 10 = 1) it emulates two SIDs and 2SID tunes play in stereo.

- No logic change. The GAL's `/SIDCS` already covers `$D400-$D7FF`; until now
  the SKpico just could not see A5, so `$D420` landed on SID #1's voice 1 and
  2SID tunes came out garbled.
- With SID #2 on, every 32-byte block with A5 set is SID #2 — `$4820` as well
  as `$D420`. Nothing written for this machine uses those.
- `$D500` tunes would need A8 on the A8 pad the same way. Not fitted.

**Verified**

- With SID #2 at `$D420` (set in RAM with SKConfig), voice 3 read back
  separately: noise on `$D41B` (`BE 74 A1`), a held oscillator on `$D43B`
  (`00 00 00`). Without the wire both would read the same chip.
- R-Type's 2SID cover played with all six voices in stereo, starting from
  SID #2 off: the converted tune's driver switched it on itself.
- Its speed was wrong at first — a CIA-timed tune, guessed at 60 Hz. Its
  init sets CIA timer A to `$2663`, 100.25 Hz; the converter now measures
  that, and the tempo matched a recording.

**Noticed**

The serial link dropped twice during testing and the machine looked hung;
the second time the PL2303 vanished from USB altogether. Check the adapter
before suspecting the machine.

---

## 2026-08-31 — GAL16V8 decoder, and the LCD back on the machine

Replaced the two 74HC138s and the 74HC00 with a single GAL16V8, and put
the HD44780 LCD back on the machine at its own address.

**Decoder**

- One GAL16V8 now generates every chip select: SID at both `$4800` and
  `$D400`, ACIA at `$5000`, LCD write strobe at `$4C00`, and ROM `/CE`
  with the `$D400` kilobyte punched out. Three chips down to one.
- Inputs A15-A10 on pins 1-6, plus PHI2 on pin 7 and R/W on pin 8.
  Outputs on pins 12-15. Four outputs and two inputs still spare.
- Sources in `gal/`: `BE6502DEC.pld` for galasm, `BE6502DEC_CUPL.PLD`
  for WinCUPL. They are different languages, not dialects — compiling
  the wrong one is a dead end. Both verified against the same reference
  model over address × PHI2 × R/W.

**ACIA**

`CS1B` (pin 3) moved to the GAL's ACIACS output; `CS0` (pin 2) stays
tied to +5V. Both must be satisfied for the chip to respond.

**LCD**

- 74LS574 octal latch at `$4C00`, driven from the data bus. `Q0-Q3` to
  LCD `D4-D7`, `Q4` to `RS`, `Q5` to `E`. `/OE` grounded, LCD `RW`
  grounded — the driver never reads, so every wait is a fixed delay.
- LS outputs only guarantee 2.4V high against the HD44780's 3.5V
  threshold, so `Q0-Q5` carry pull-ups to +5V.
- The strobe is **`LCDWR`, not a plain address decode**: the latch
  clocks on a rising edge, and the address bus is only valid while PHI2
  is high, so a transient through the `$4C00` window between cycles
  would clock garbage in. Qualifying with PHI2 and write fixes that and
  also stops it strobing on reads.

**Verified**

- Smoke tests play at both `$4800` and `$D400`, and several tunes run,
  so the GAL reproduces the discrete logic exactly.
- `LCDTest.bin` prints both lines.
- Bring-up went through a hand-typed init sequence at `$4C00` from
  WozMon first. Worth remembering as a technique: every address in the
  1K window hits the latch, so WozMon's multi-byte write form gives
  consecutive strobes, and typing speed exceeds every HD44780 minimum.
  That isolates the wiring from the code completely.

The one real fault was loose wiring, found only after the manual test
proved the data path was sound.

---

## 2026-08-29 — SID also at `$D400`

Added a second 74HC138 and a 74HC00 so the SIDKick pico answers at its native
C64 address as well as `$4800`. This is what lets `.sid` files from the archives
run without patching their code — a compiled blob's SID accesses cannot be
reliably found and rewritten, so the hardware moves instead.

**New hardware**

- **74HC138 #2** — `G1`←A15, `/G2A`←`/A14`, `/G2B`←A13 gives a `$C000-$DFFF`
  window; `C,B,A`←A12,A11,A10 with `101` selects **Y5 = `$D400-$D7FF`**.
  Wired identically to decoder 1 apart from A15/A14 trading pins 4 and 6, A14
  arriving inverted, and the tap moving from Y2 (pin 13) to Y5 (pin 10).
- **74HC00** — gate 1 inverts A14 for `/G2A`; gates 2+3 form `AND(Y2, Y5)` =
  SID `/CS`; gate 4 forms `NAND(A15, Y5)` = ROM `/CE`.

**Rewired**

- Decoder 1 pin 13 no longer goes straight to SID `/CS`; it is now an AND input.
- ROM `/CE` now comes from the 74HC00 rather than its previous source.

**Verified**

- Register-poke smoke test plays at **both** `$4800` and `$D400`.
- WozMon still responds, which proves the rewired ROM `/CE` did not break the
  ROM either side of the new 1 KB hole.
- Decode equations were checked exhaustively in software beforehand across all
  65536 addresses: no address selects both the SID and ROM.

**Cost**

None in ROM. `$D400-$D7FF` already sat inside a 22 KB run of zeros and the ROM
source has no references to `$D000-$DFFF`. If the program ever grows past
`$D400`, that hole has to be stepped over.

See [MEMORY_MAP.md](MEMORY_MAP.md) for pinouts, and `gal/BE6502DEC.pld` for the
single-GAL16V8 version that would replace all three logic chips.

---

## 2026-08-27 — SIDKick pico at `$4800`

Added a SIDKick pico 0.2 DAC as a SID replacement.

**New hardware**

- 74HC138 3-to-8 decoder generating every I/O chip select in `$4000-$5FFF`.
  Enables wired `G1`←A14, `/G2A`←A15, `/G2B`←A13, so the chip is only awake for
  `A15:A14:A13 = 010`. Selects `C,B,A`←A12,A11,A10 split the window into eight
  1K slots. **Y2 → SID `/CS`** (pin 8). No extra glue gates were needed, which
  mattered — the SD pulse generator had consumed every spare 74HC14 and
  74HCT08 gate.
- SIDKick pico 0.2 DAC. Board is a 28-pin DIP footprint with **pins 1-4
  unpopulated** (filter caps are simulated in firmware), hence the 10-pin and
  14-pin headers. `/RES`, `φ2`, `R/W`, `/CS`, `A0-A4`, `D0-D7`, GND, +5V.
  Pins 23, 24, 26, 27, 28 left unconnected — no +12V required.
- Audio taken from the `GND-L-GND-R` line-out header into powered speakers.
  Pin 27 audio out deliberately **not** used; it is inert unless the "L" solder
  jumper is closed, and that path is known to distort.

**Notes**

- Clock swapped to 1 MHz. The SKpico will not latch the bus at 5 MHz, and reSID
  is clocked from PHI2 so pitch would be 5× high regardless.
- Confirmed there is no RAM in `$4000-$5FFF` — RAM is enabled only when
  A15=0 and A14=0 — so reads from the SID region do not contend.
- Worked on first power-up: register-poke smoke test, arpeggio test program and
  Monty on the Run all played without troubleshooting.

---

## Prior state

Undated — carried over from the upstream projects before this log started.

- Fast SD card interface: two 74HC595 shift registers (one re-purposed from the
  PS/2 keyboard hardware, one as a pulse generator) plus 74HC14 inverters and
  spare 74HCT08 AND gates from the VGA hardware. ~10 CPU cycles per byte at
  5 MHz.
- VGA output changed from RRGGBB 64-colour to RRRGGGBB 256-colour.
- W65C51 ACIA at `$5000`.
- System clock raised to 5 MHz for the SD streaming demos.
