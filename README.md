# BE6502 Build

Documentation for my Ben Eater 6502 breadboard computer.

This repo describes **the machine**, not any one program that runs on it. The
software projects it runs live in their own repos (see [Built from](#built-from)) —
what is documented here is the hardware: what is on the breadboards, how the
address space is carved up, and what changed when.

Start with **[MEMORY_MAP.md](MEMORY_MAP.md)** — memory map, the address
decoding, SIDKick pico and LCD wiring, bring-up tests and 50 Hz timing.
**[BUILD_LOG.md](BUILD_LOG.md)** is the dated record of hardware changes.
**[PSID_RSID_AND_THE_KERNAL.md](PSID_RSID_AND_THE_KERNAL.md)** explains why
half of HVSC will never run here — interrupt vectors, what a KERNAL actually
is, and why this machine has firmware instead of one.

## The machine

| | |
|---|---|
| CPU | W65C02S |
| Clock | 1 MHz for SID work, 5 MHz for the SD demos (swappable can oscillator) |
| RAM | 62256 SRAM, 16K decoded at `$0000-$3FFF` |
| ROM | 28C256 EEPROM, 32K at `$8000-$FFFF`, WozMon at `$F700` |
| I/O decode | 74HC138, 8 × 1K slots across `$4000-$5FFF` |
| Sound | SIDKick pico 0.2 DAC at `$4800` (74HC138 Y2) |
| Serial | W65C51 ACIA at `$5000` |
| Timers / ports | 6522 VIA at `$6000` |
| Video | Worlds Worst Video Card, 100×64 at `$2000`, 256 colour as BBGGGRRR (blue in the top bits — tested, not RRRGGGBB) |
| Storage | SD card via shift-register fast interface on VIA ports A/B |

## State

Working:

- SIDKick pico plays through the DAC line-out. Verified with a register-poke
  smoke test, an arpeggio test program, Monty on the Run, and 15 tunes
  auto-ported from the realdmx C64 player collection.
- Fast SD video/audio streaming at 5 MHz.
- Serial binary load into RAM (`<addr>L`) from WozMon.

Not done yet:

- The SID and the 5 MHz SD hardware have not been run together — the SKpico
  needs ~1 MHz PHI2, so the clock has to be swapped between the two.
- Six of the eight 74HC138 I/O slots are unassigned.

## Tools

In `code/`:

| file | what it does |
|---|---|
| `LCDTest.asm` | standalone HD44780 bring-up at `$0400`. Load `400L`, run `400R`. |
| `SKConfig.asm` | reads and sets the SIDKick pico's configuration over serial — the job the C64's `SYS 54301` menu does: SID #1 and #2 chip, SID #2 address, PAL/NTSC. Changes are RAM-only until `s` saves them. Load `400L`, run `400R`. |
| `be6502.py` | serial terminal and `.bin` loader for the Mac, with TeraTerm's timing: 1 ms per byte for the Fast Binary Load, 30 ms per typed character for WozMon. `load SKConfig.bin 400 --run`, or `term`. Needs pyserial. |
| `SidToBE6502.py` | turns a `.sid` file into a binary that runs here. Emits ACME source — tune blob, polled 50/60 Hz driver, LCD routines, title and author strings — and lets ACME assemble it. Reports the chip, clock and second SID the tune was written for, and the driver sets the SKpico to match in RAM before init (`--no-skpico` to leave it alone). CIA-timed tunes are run in py65 to measure their call rate — `pip install py65`, or it guesses 60 Hz. py65 also finds memory a tune reads before writing, which a C64 would have zeroed, and the driver clears it before init. |
| `hvsc_fetch.py` | pulls tunes from HVSC by composer or path match, filtering to what this machine can actually play — including 2SID tunes whose second SID the SKpico is wired for. Tunes that load too high are moved to `$1000` with sidreloc first. Tags each with its chip and clock. `--convert` runs the converter on each keeper. |
| `sidreloc/` | [sidreloc](https://www.linusakesson.net/software/sidreloc/) 1.0 by Linus Åkesson (MIT), vendored so it builds on both machines: `make -C code/sidreloc`. It relocates a compiled tune and verifies the move by playing both copies side by side. 44 of Rob Hubbard's 47 tunes that loaded too high run here because of it. |

```bash
make -C sidreloc
python hvsc_fetch.py --composer Hubbard_Rob --runnable --convert
```

These live here rather than in the SID player fork because they encode
**this machine**: the SID at `$D400`, the LCD latch at `$4C00`, the VIA at
`$6000`, and RAM ending at `$4000`. They would not do anything useful on
another build.

`PortSidToBE6502.py` is the exception and stays in the fork — it ports
that repo's own ACME sources and would help anyone who clones it.

## Built from

Upstream projects this machine runs, each with its own repo:

- [NormalLuser/BE6502-Fast-SD-Card-Interface](https://github.com/NormalLuser/BE6502-Fast-SD-Card-Interface)
  — the shift-register SD interface and the 256-colour VGA mod.
- [NormalLuser/BE6502_SidPlayer](https://github.com/NormalLuser/BE6502_SidPlayer)
  — SID player sources. My port tooling and the ported tunes live in my fork.
- [Fifty1Ford/Ben-Eater-Bad-Apple](https://github.com/Fifty1Ford/Ben-Eater-Bad-Apple)
  — Bad Apple demo.
- [Ben Eater's 6502 project](https://eater.net/6502) — the base computer.

Changes that would help anyone using those projects belong upstream, in a fork
of the project in question. Changes that only make sense on this machine belong
here.
