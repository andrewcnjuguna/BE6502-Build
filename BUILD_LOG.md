# Build log

Hardware changes to the machine, newest first. Software changes live in the
project repos, not here.

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
