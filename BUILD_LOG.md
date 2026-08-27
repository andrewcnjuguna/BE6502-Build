# Build log

Hardware changes to the machine, newest first. Software changes live in the
project repos, not here.

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

See [MEMORY_MAP.md](MEMORY_MAP.md) for the full decode and wiring detail.

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
