r"""
Fetch tunes from HVSC by criteria - composer, path match, or both.

RUN IT WITH  py -3  ON THIS MACHINE. Plain `python` is MSYS2's
(C:\\msys64\\usr\\bin\\python.exe), which treats a D:\\... script path as
relative and reports the file as missing. There is deliberately no
"#!/usr/bin/env python3" line here either - the py launcher honours
shebangs, and that one sends it straight back to the MSYS interpreter.

rebuild_ec64sc_v2.py could not do this because its input is one curated
web page: it scrapes the links that page happens to contain. There is no
notion of a composer, because there is no index of the collection to
filter. This script adds the missing piece.

HVSC ships a complete index of itself at DOCUMENTS/Songlengths.md5. It is
an INI-style file whose comment lines carry the full path of every .sid in
the collection:

    [Database]
    ; /MUSICIANS/H/Hubbard_Rob/Commando.sid
    <md5>=3:14

Download that once, filter the paths, fetch only what matched.

Examples
    py -3 hvsc_fetch.py --composers H            list composers under H
    py -3 hvsc_fetch.py --composer Hubbard_Rob --runnable
    py -3 hvsc_fetch.py --match "Exploding_Fist" --dry-run
    py -3 hvsc_fetch.py --composer Tel_Jeroen --runnable --convert
          --out D:\C64\tunes --convert-out D:\C64\tunes\be6502

--runnable keeps only tunes this machine can actually play: PSID, a
non-zero play address, small enough to sit under $4000 with the driver,
and for a 2SID tune a second SID the SKpico can be wired for (see
WIRED_PADS in SidToBE6502.py). Roughly half of HVSC fails that, so it is
worth applying before downloading rather than after.

Each download is tagged with the chip and clock it was written for, and
its second SID if it has one - "6581 PAL", "8580 PAL 2SID $D420" - in
the listing and in manifest.csv. The converted driver sets the SKpico
to match, in RAM, so these are for reference.

Tunes that load where this machine has no RAM - most famous ones sit at
$8000-$FFFF - are relocated to $1000 with sidreloc (Linus Akesson's,
source in sidreloc/, `make` there to build it). sidreloc plays the tune
and the moved copy side by side for half an hour of music and only
accepts the result if every SID write matches, so a move that succeeds
sounds the same. The moved tune is what gets saved and converted; the
HVSC original goes in <out>/original/, and manifest.csv says what moved
where. Without sidreloc - or with --no-relocate - those tunes are skipped
as before. 44 of Rob Hubbard's 47 too-high tunes moved this way.

--convert runs SidToBE6502.py on each keeper. Downloaded .sid files go
to --out; the .bin/.asm/.sym they produce go to --convert-out, which
defaults to a "converted" subdirectory of --out so the two never mix.

Standard library only, like the script it grew out of.
"""

import argparse
import csv
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError

# the converter's own header checks, so the two cannot disagree
from SidToBE6502 import header_info, describe, sid2_problem

MIRRORS = [
    "https://hvsc.perff.dk",
    "https://hvsc.c64.org/download/C64Music",
    "https://www.hvsc.c64.org/download/C64Music",
]
INDEX_PATH = "/DOCUMENTS/Songlengths.md5"
INDEX_CACHE = Path("Songlengths.md5")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BE6502-hvsc-fetch/1.0"

DRIVER_ROOM = 220          # driver + SKpico setup (213); the LCD code is optional
RAM_TOP = 0x4000
RELOC_BASE = 0x1000        # where sidreloc moves a tune; $0300-$0FFF stays free


def make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CONTEXT = make_ssl_context()


def get_bytes(url, timeout=30):
    """Fetch a URL, retrying without certificate verification only when
    verification itself is what failed. Some Windows Python installs have
    an incomplete CA bundle for sites the browser trusts fine."""
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
            return r.read()
    except URLError as e:
        reason = getattr(e, "reason", None)
        cert_error = (isinstance(reason, ssl.SSLCertVerificationError)
                      or "CERTIFICATE_VERIFY_FAILED" in str(e))
        if not cert_error:
            raise
        with urlopen(req, timeout=timeout,
                     context=ssl._create_unverified_context()) as r:
            return r.read()


def quote_path(p):
    """Quote each component, keeping the punctuation HVSC filenames use."""
    return "/".join(quote(x, safe="()[],'&+_-.") for x in p.split("/"))


def load_index(refresh=False):
    if INDEX_CACHE.exists() and not refresh:
        raw = INDEX_CACHE.read_bytes()
    else:
        raw = None
        for base in MIRRORS:
            url = base + INDEX_PATH
            try:
                print("fetching index from %s" % url)
                raw = get_bytes(url)
                break
            except Exception as e:
                print("   failed: %s" % e)
        if raw is None:
            sys.exit("could not fetch the index from any mirror")
        INDEX_CACHE.write_bytes(raw)
        print("cached as %s (%d KB)" % (INDEX_CACHE, len(raw) // 1024))

    text = raw.decode("latin-1")
    paths = re.findall(r"^;\s*(/.+?\.sid)\s*$", text, re.M | re.I)
    if not paths:
        sys.exit("index parsed but no .sid paths found - format may have changed")
    return paths


def tune_span(data):
    """(load address, length) of the tune body, or None if truncated."""
    off, load = struct.unpack(">HH", data[6:10])
    body = data[off:]
    if load == 0:
        if len(body) < 2:
            return None
        load = struct.unpack("<H", body[:2])[0]
        body = body[2:]
    return load, len(body)


def sid_verdict(data):
    """Mirror the checks in SidToBE6502.py. Returns (reason, movable):
    reason is None if the tune runs as it is, and movable is True when the
    only thing wrong is where it loads - which sidreloc can fix."""
    if len(data) < 0x20 or data[:4] not in (b"PSID", b"RSID"):
        return "not a PSID/RSID", False
    if data[:4] == b"RSID":
        return "RSID, needs the KERNAL", False
    span = tune_span(data)
    if span is None:
        return "truncated", False
    load, length = span
    if struct.unpack(">H", data[0x0C:0x0E])[0] == 0:
        return "play address 0, drives its own IRQ", False
    problem = sid2_problem(header_info(data))
    if problem:
        return problem.rstrip("."), False
    end = load + length
    if load >= 0x200 and end + DRIVER_ROOM <= RAM_TOP:
        return None, False
    why = ("loads over zero page/stack" if load < 0x200
           else "needs $%04X, past RAM" % (end + DRIVER_ROOM))
    if RELOC_BASE + length + DRIVER_ROOM > RAM_TOP:
        return "%s, and at %d bytes too big to move under $4000" % (why, length), False
    return why, True


def find_sidreloc():
    """$SIDRELOC, then sidreloc on the PATH, then the build in sidreloc/."""
    here = Path(__file__).resolve().parent / "sidreloc"
    for c in (os.environ.get("SIDRELOC"), shutil.which("sidreloc"),
              here / "sidreloc", here / "sidreloc.exe"):
        if c and Path(c).is_file() and os.access(str(c), os.X_OK):
            return str(c)
    return None


def relocate(data, tool):
    """Move the tune to RELOC_BASE. Returns (new data, note) on success -
    note is "" or a warning - and (None, why) when sidreloc gives up."""
    with tempfile.TemporaryDirectory() as d:
        src, dst = Path(d, "in.sid"), Path(d, "out.sid")
        src.write_bytes(data)
        r = subprocess.run([tool, "-p", "%02x" % (RELOC_BASE >> 8), str(src), str(dst)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = [l.strip() for l in r.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
        last = lines[-1] if lines else "sidreloc exited %d" % r.returncode
        if dst.exists() and r.returncode == 0:
            return dst.read_bytes(), ""
        if dst.exists() and "mismatching pitches" in last:
            return dst.read_bytes(), "a few pitches off after the move"
        return None, last.replace("sidreloc: ", "")


def composers(paths, letter=None):
    out = {}
    for p in paths:
        m = re.match(r"/MUSICIANS/([^/])/([^/]+)/", p, re.I)
        if m and (letter is None or m.group(1).upper() == letter.upper()):
            out[m.group(2)] = out.get(m.group(2), 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--composer", help="MUSICIANS subdirectory, partial and case-insensitive")
    ap.add_argument("--match", help="regex matched against the whole path")
    ap.add_argument("--composers", metavar="LETTER", nargs="?", const="",
                    help="list composers (optionally under one letter) and exit")
    ap.add_argument("--out", default="hvsc_out",
                    help="where downloaded .sid files go (default hvsc_out)")
    ap.add_argument("--convert-out", dest="convert_out",
                    help="where converted .bin/.asm go (default <out>/converted)")
    ap.add_argument("--runnable", action="store_true",
                    help="keep only tunes this machine can play")
    ap.add_argument("--convert", action="store_true",
                    help="run SidToBE6502.py on each keeper")
    ap.add_argument("--no-relocate", dest="relocate", action="store_false",
                    help="skip tunes that load too high instead of moving them with sidreloc")
    ap.add_argument("--limit", type=int, help="stop after N downloads")
    ap.add_argument("--refresh-index", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="list matches, download nothing")
    args = ap.parse_args()

    paths = load_index(args.refresh_index)
    print("index lists %d tunes" % len(paths))

    if args.composers is not None:
        found = composers(paths, args.composers or None)
        for name in sorted(found):
            print("   %-40s %4d tunes" % (name, found[name]))
        print("\n%d composers" % len(found))
        return 0

    if not args.composer and not args.match:
        ap.error("give --composer, --match, or --composers")

    sel = paths
    if args.composer:
        pat = re.compile(r"/MUSICIANS/[^/]/[^/]*%s[^/]*/" % re.escape(args.composer), re.I)
        sel = [p for p in sel if pat.search(p)]
    if args.match:
        rx = re.compile(args.match, re.I)
        sel = [p for p in sel if rx.search(p)]

    print("%d paths match" % len(sel))
    if not sel:
        return 1
    if args.dry_run:
        for p in sel:
            print("   " + p)
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, kept, skipped, failed, moved_n, unmoved = [], 0, 0, 0, 0, 0
    sidreloc = find_sidreloc() if args.relocate else None
    if args.relocate and not sidreloc:
        print("sidreloc not found - tunes that load too high will be skipped.\n"
              "Build it with `make` in %s, or set SIDRELOC.\n"
              % (Path(__file__).resolve().parent / "sidreloc"))

    for i, path in enumerate(sel, 1):
        if args.limit and kept >= args.limit:
            break
        data, used = None, ""
        for base in MIRRORS:
            url = base + quote_path(path)
            try:
                data = get_bytes(url)
                used = url
                break
            except Exception:
                continue
        if data is None:
            print("[%3d/%3d] FAIL %s" % (i, len(sel), path))
            failed += 1
            rows.append({"path": path, "status": "FAILED", "reason": "no mirror served it",
                         "filename": "", "url": ""})
            continue

        reason, movable = sid_verdict(data)
        original, moved = None, ""
        if reason and movable and args.relocate and not sidreloc:
            unmoved += 1
        if reason and movable and sidreloc:
            print("[%3d/%3d] move %-46s %s - relocating to $%04X ..."
                  % (i, len(sel), os.path.basename(path), reason, RELOC_BASE))
            new, note = relocate(data, sidreloc)
            if new is None:
                reason = "%s; sidreloc: %s" % (reason, note)
            else:
                (a, n), (b, _) = tune_span(data), tune_span(new)
                original, data = data, new
                moved = "$%04X-$%04X to $%04X%s" % (a, a + n - 1, b, "; " + note if note else "")
                moved_n += 1
                reason, _ = sid_verdict(data)
        if args.runnable and reason:
            print("[%3d/%3d] skip %-46s %s" % (i, len(sel), os.path.basename(path), reason))
            skipped += 1
            rows.append({"path": path, "status": "skipped", "reason": reason,
                         "filename": "", "url": used})
            continue

        name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(path))
        dest = out / name
        dest.write_bytes(data)
        if original:
            (out / "original").mkdir(exist_ok=True)
            (out / "original" / name).write_bytes(original)
        kept += 1
        tag = describe(header_info(data)) if data[:4] in (b"PSID", b"RSID") else ""
        print("[%3d/%3d] OK   %-46s %s%s%s" % (i, len(sel), name, tag,
                                               "   (moved %s)" % moved if moved else "",
                                               "" if not reason else "   (%s)" % reason))
        rows.append({"path": path, "status": "downloaded", "reason": reason or "",
                     "chip": tag, "relocated": moved, "filename": name, "url": used})
        time.sleep(0.05)

    with (out / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["path", "status", "reason", "chip", "relocated",
                                          "filename", "url"], restval="")
        w.writeheader()
        w.writerows(rows)

    print("\n%d downloaded (%d relocated), %d skipped, %d failed"
          % (kept, moved_n, skipped, failed))
    if unmoved:
        print("%d of the skipped would have run moved to $%04X - build sidreloc"
              % (unmoved, RELOC_BASE))
    print("folder: %s" % out.resolve())

    if args.convert and kept:
        conv = Path(__file__).with_name("SidToBE6502.py")
        cout = Path(args.convert_out) if args.convert_out else out / "converted"
        cout.mkdir(parents=True, exist_ok=True)
        print("\nconverting into %s" % cout.resolve())
        ok = 0
        for row in rows:
            if row["status"] != "downloaded":
                continue
            # absolute source path, but run with cwd=cout so every output
            # the converter writes - .asm, .bin, .sym, its temp .dat - lands
            # there instead of beside the .sid files
            src = str((out / row["filename"]).resolve())
            r = subprocess.run([sys.executable, str(conv), src],
                               cwd=str(cout), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
            text = r.stdout.decode("utf-8", "replace")
            line = next((l for l in text.splitlines() if "WozMon" in l), "")
            if r.returncode == 0:
                ok += 1
                print("   %-44s %s" % (row["filename"], " ".join(line.split()[1:])))
            else:
                why = next((l.split("PROBLEM", 1)[1].strip()
                            for l in text.splitlines() if "PROBLEM" in l),
                           text.strip().splitlines()[-1] if text.strip() else "?")
                print("   %-44s FAILED  %s" % (row["filename"], why[:50]))
        print("converted %d of %d" % (ok, kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
