# Why some SID tunes will never run here

Roughly half of HVSC cannot play on this machine. Most of those are simply
too big for 16 KB of RAM, which needs no explanation. But a second group
fails for a more interesting reason, and understanding it explains a lot
about how 8-bit machines actually work.

---

## The distinction: who owns the timing

A SID tune has to be told to advance, about fifty times a second. The only
question is **who does the telling**, and that single question is what
splits the two file formats apart.

```
PSID - a subroutine you call
+----------------------------------------------------+
|  your driver              the tune                  |
|                                                     |
|  JSR init      ---------> set up voices, RTS        |
|                                                     |
|  , JSR play    ---------> write $D400 regs, RTS     |
|  | poll the VIA T1 flag                             |
|  ` 50 times a second                                |
+----------------------------------------------------+
      YOU own the timing -> runs on anything with a SID

RSID - a program that wants the machine
+----------------------------------------------------+
|  JSR init      ---------> SEI                       |
|                           STA $0314   <- patch the  |
|                           STA $0315      IRQ vector |
|                           arm a CIA timer or raster |
|                           CLI                       |
|                           RTS                       |
|                                                     |
|  ...and there is no play address to call. The tune  |
|  expects interrupts to arrive and drive it forever. |
+----------------------------------------------------+
      the MACHINE owns the timing -> needs a real C64
```

That is why **every RSID file carries a play address of 0**. It is not
missing data. There is genuinely nothing to call: the tune is not a
subroutine, it is a resident program.

PSID came first and is a promise - *"I am a well-behaved subroutine, call
me at 50 Hz."* RSID exists because some tunes cannot honestly make that
promise, and says instead: *"give me a real C64 and get out of the way."*

---

## How a tune "installs its own interrupt"

This is worth following through properly, because the mechanism is
general 6502, not a C64 quirk.

### The hardware part

A 6502 has three vectors hard-wired into the top of its address space:

| address | vector | when it is used |
|---|---|---|
| `$FFFA/$FFFB` | NMI | non-maskable interrupt pin goes low |
| `$FFFC/$FFFD` | RESET | power-on, or the reset button |
| `$FFFE/$FFFF` | IRQ | interrupt pin low and the I flag is clear |

When an interrupt happens the CPU pushes the program counter and status
register onto the stack, **reads a 16-bit address out of the vector**, and
jumps there. That is the entire mechanism. The CPU has no idea what an
operating system is; it just reads two bytes and jumps.

Those three vectors are the fundamental hardware contract of the chip.
Every 6502 machine ever built has them, including this one.

### The software part

`$FFFE` lives in ROM, so on a finished machine it points at whatever the
ROM author wrote. That would make interrupt handling unchangeable - so the
C64's KERNAL adds a **second, indirect jump through a RAM vector**:

```
  interrupt
      |
      v
  $FFFE/$FFFF ------> KERNAL handler in ROM
                          |
                          | JMP ($0314)      <- indirect through RAM
                          v
                      whatever address is stored at $0314/$0315
                          |
                          `-> normally the KERNAL's own routine:
                              scan keyboard, update the clock, ...
```

`$0314` is ordinary RAM, so any program can overwrite it. That is the
hook. Installing a music player is then just:

```asm
        SEI                 ; interrupts off while we meddle
        LDA #<myplayer
        STA $0314           ; point the RAM vector at our code
        LDA #>myplayer
        STA $0315
        ; arm a hardware source: a CIA timer, or a VIC raster compare
        CLI                 ; interrupts on
        RTS                 ; init is finished - the tune runs itself now
```

Two things had to be true for that to work, and **both are properties of
the machine, not the tune**:

1. Something must actually honour the RAM vector at `$0314`.
2. A hardware source must exist that generates interrupts.

---

## What happens when you run one here

Each step fails silently:

```
  STA $0314        -> ordinary RAM. Nothing on this machine reads it.
  arm CIA $DC04    -> $Dxxx is ROM space here. The write is ignored.
  arm raster $D012 -> same.
  CLI              -> interrupts enabled, but nothing will ever assert IRQ.
  RTS              -> returns. Nothing further happens, ever.
```

Not a crash. Not a hang. Just permanent silence, which is exactly what you
observe. The tune did everything correctly; the machine underneath it was
not the one it was written for.

`SidToBE6502.py` rejects these up front rather than producing a binary
that loads fine and does nothing.

### The same question, in miniature, on this machine

This is not an abstract problem - the Monty on the Run port ran straight
into it. That player uses `WAI` to sleep until an interrupt, and the fix
was `SEI` plus `BIT T1C_L`: keep the I flag set so `WAI` **resumes inline**
instead of vectoring into this machine's ROM handler, which knows nothing
about clearing the VIA's timer flag.

The polled driver used by both converters settles it permanently by never
enabling an interrupt at all:

```asm
-       BIT VIA_IFR        ; V flag <- bit 6 = T1 timed out
        BVC -
        BIT VIA_T1CL       ; reading T1C_L clears the flag
```

No vector, no handler, nothing to fight over. That is precisely why PSID
tunes work here and RSID tunes cannot: **the polled driver can only drive
a tune that agrees to be driven.**

---

## What a KERNAL is, and why this machine has not got one

Commodore spelled it that way on purpose. It is not "kernel" in the
operating-system sense.

The C64 KERNAL is 8 KB of ROM at `$E000-$FFFF` holding the reset code, the
interrupt dispatcher described above, and routines for keyboard, screen,
serial disk and tape. The important part is not the routines themselves
but that they are reachable through a **jump table at fixed addresses that
have not moved since 1982**:

| call | does |
|---|---|
| `JSR $FFD2` | print a character |
| `JSR $FFE4` | read a key |
| `JSR $FFD5` | load a file |
| `JSR $FFD8` | save a file |

Any C64 program can hard-code those and be certain they will be there.
That published contract is what allowed a software industry to exist for
the machine.

### Firmware, monitor, KERNAL, OS

Not every computer has a KERNAL, and the reason it feels universal is that
modern machines all boot an operating system. There is a spectrum:

```
  bare CPU        needs only a RESET vector saying where to begin
     |
     v
  firmware        code in ROM that runs the hardware
     |            (this machine: WozMon, the SD and video routines)
     v
  firmware with   fixed, documented entry points third-party
  a published     software can call
  API             (the C64 KERNAL, the IBM PC BIOS)
     |
     v
  operating       processes, memory protection, drivers, filesystems
  system
```

This machine sits at the second level. It has:

- a RESET vector at `$FFFC` - the hard requirement, present on every 6502
- IRQ and NMI vectors pointing at handlers in ROM
- WozMon, about 250 bytes: examine memory, change it, run something
- NormalLuser's SD card and video routines

Those routines are real and useful, but they live at addresses chosen for
this build, with no standard interface anyone else writes against. That is
firmware. A KERNAL is firmware plus a promise.

| | C64 | this machine |
|---|---|---|
| ROM | `$E000-$FFFF`, 8 KB | `$8000-$FFFF`, 32 KB |
| contains | the KERNAL | WozMon + SD/video code |
| RESET vector | yes | yes |
| IRQ dispatch | via the RAM vector `$0314` | straight into the ROM handler |
| published API | ~39 fixed entry points | none |

### Could this machine get one?

Not usefully. "Just provide the API" means writing a fresh implementation
of a 1982 interface - the KERNAL ROM itself is Commodore's code - and the
tunes that would benefit are mostly the ones too large for 16 KB anyway.

The practical answer is the filter already in use:

```bash
py -3 hvsc_fetch.py --composer Hubbard_Rob --runnable
```

`--runnable` reads each header and rejects RSID files, tunes with a play
address of 0, and anything that will not fit under `$4000` - before
downloading rather than after.

---

## The general lesson

Three ideas here are worth more than the SID trivia:

- **Interrupt vectors are a hardware contract.** The CPU reads an address
  from a fixed location and jumps. Everything above that - handlers,
  dispatchers, drivers - is a convention built on those few bytes.

- **An indirection in RAM is what makes firmware extensible.** The C64's
  `JMP ($0314)` is the only reason a program could take over interrupt
  handling without replacing the ROM. The same trick appears everywhere:
  interrupt tables, vtables, plugin registries, syscall tables.

- **"Who owns the timing" is a real design decision**, and it decides
  portability. A subroutine that gets called runs anywhere. A program that
  installs itself into the machine's interrupt system only runs on that
  machine. Both patterns are still everywhere in embedded work - polling
  versus interrupt-driven is the same choice, and this build made it
  deliberately in favour of polling.
