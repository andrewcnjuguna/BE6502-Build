"""Growing the library: tunes from HVSC, pictures from game covers.

Both run the repo's own tools, as the CLI does:
- code/hvsc_fetch.py downloads from HVSC, keeps only what this machine
  can play, moves too-high tunes to $1000 with sidreloc, and converts each
  with SidToBE6502.py. Needs acme on the PATH, sidreloc built
  (make -C code/sidreloc), and py65 for CIA-timed tunes.
- code/img2be6502.py turns an image into an 8 KB screen and a preview PNG.
  Needs Pillow.

Everything lands under settings.DOWNLOAD_DIR, inside the tune library, so
list_tunes and list_pictures find it straight away.

What a web page says never reaches the model - only image file names,
sizes and URLs, and only images from C64-Wiki and Wikimedia are fetched.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import machine, settings
from .machine import MachineError, _captured

CODE = settings.REPO_ROOT / "code"
import hvsc_fetch  # noqa: E402  (code/ is on sys.path via machine)

hvsc_fetch.INDEX_CACHE = CODE / "Songlengths.md5"  # not the current directory

UA = "BE6502-agent/1.0 (hobby project; one request at a time)"
MAX_IMAGE = 15 * 1024 * 1024
MAX_FETCH = 8  # tunes per fetch_hvsc call
IMAGE_HOSTS = {"www.c64-wiki.com", "c64-wiki.com", "upload.wikimedia.org"}

_index: list[str] | None = None


def _get(url: str, limit: int = 4 * 1024 * 1024, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise MachineError(f"{url} is over {limit // (1024 * 1024)} MB")
    return data


def _clean(name: str) -> str:
    """A wiki file name, made safe to show the model."""
    return re.sub(r"[^\w .,()'&+-]", "", urllib.parse.unquote(name))[:80]


# --- HVSC ----------------------------------------------------------------------

def _paths() -> list[str]:
    global _index
    if _index is None:
        with _captured():
            _index = hvsc_fetch.load_index()
    return _index


def search_hvsc(query: str, limit: int = 30) -> str:
    words = query.lower().replace("_", " ").split()
    if not words:
        raise MachineError("give some words to look for, e.g. 'hubbard commando'")
    paths = _paths()
    hits = [p for p in paths if all(w in p.lower().replace("_", " ") for w in words)]
    head = f"{len(hits)} of {len(paths)} HVSC tunes match {query!r}"
    if len(hits) > limit:
        head += f" (first {limit} - add words to narrow it)"
    return "\n".join([head] + hits[:limit])


def fetch_hvsc(paths: list[str]) -> str:
    if not paths:
        raise MachineError("no paths given")
    if len(paths) > MAX_FETCH:
        raise MachineError(f"at most {MAX_FETCH} tunes at a time")
    known = set(_paths())
    unknown = [p for p in paths if p not in known]
    if unknown:
        raise MachineError("not HVSC paths (copy them exactly from search_hvsc): " + ", ".join(unknown))
    out = settings.DOWNLOAD_DIR / "hvsc"
    conv = out / "converted"
    rx = "^(" + "|".join(re.escape(p) for p in paths) + ")$"
    r = subprocess.run(
        [sys.executable, str(CODE / "hvsc_fetch.py"), "--match", rx, "--runnable", "--convert",
         "--out", str(out), "--convert-out", str(conv)],
        cwd=CODE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800,
    )
    text = r.stdout.decode("utf-8", "replace")
    keep = [l.strip() for l in text.splitlines()
            if re.search(r"\] (OK|skip|move|FAIL)|FAILED|converted \d|downloaded|sidreloc not found", l)]
    new = []
    for p in paths:
        stem = re.sub(r"[^A-Za-z0-9]+", "_", Path(p).stem).strip("_")
        # _songN when the tune's default song is not the first
        for b in [conv / f"BE6502_{stem}.bin", *sorted(conv.glob(f"BE6502_{stem}_song*.bin"))]:
            if b.exists():
                new.append(machine._describe(b, machine.be6502.origin(str(b))))
    result = "\n".join(keep) or text.strip()[-1500:]
    if new:
        result += "\nNow in the library, ready for play_tune:\n" + "\n".join(new)
    else:
        result += "\nNothing new is playable."
    return result


# --- pictures ------------------------------------------------------------------

_SKIP = re.compile(r"^(flag|wp-w\d|joystick|vote|hiscore|hs_|cami_high)|icon|highscore|_hsc|_hs\.|"
                   r"record|werbung|advert", re.I)


def _rank(name: str) -> int:
    n = name.lower()
    if re.search(r"cover|box|packshot", n):
        return 0
    if re.search(r"title|titel|intro|loading|logo", n):
        return 1
    return 2


def _c64wiki(game: str) -> tuple[str | None, list[tuple[str, str]]]:
    """(page, [(file name, url)]). C64-Wiki turns scripts away from its
    search and API, so try likely page names directly."""
    base = game.strip().replace(" ", "_")
    tries = [base, base.title(), "The_" + base.title(), re.sub(r"^(?i:the_)", "", base.title())]
    for title in dict.fromkeys(tries):
        url = "https://www.c64-wiki.com/wiki/" + urllib.parse.quote(title)
        try:
            page = _get(url).decode("utf-8", "replace")
        except urllib.error.HTTPError:
            continue
        found = {}
        for m in re.finditer(r'(?:src|href)="(?:https://www\.c64-wiki\.com)?(/images/[^"]+?\.(?:png|jpe?g|gif))"',
                             page, re.I):
            path = re.sub(r"^/images/thumb/([^/]+/[^/]+/[^/]+)/.*$", r"/images/\1", m.group(1))
            name = path.rsplit("/", 1)[-1]
            if not _SKIP.search(name):
                found[name] = "https://www.c64-wiki.com" + path
        return title, sorted(found.items(), key=lambda kv: _rank(kv[0]))
    return None, []


def _wikipedia(game: str) -> tuple[str | None, list[tuple[str, str, str]]]:
    """(article, [(file name, size, url)]), the infobox image - usually the
    cover - first."""
    api = "https://en.wikipedia.org/w/api.php?"

    def call(**q):
        q.update(format="json", formatversion="2")
        return json.loads(_get(api + urllib.parse.urlencode(q)))

    hits = call(action="query", list="search", srsearch=f"{game} video game", srlimit=1)["query"]["search"]
    if not hits:
        return None, []
    title = hits[0]["title"]
    lead = call(action="query", titles=title, prop="pageimages", piprop="original")["query"]["pages"][0]
    lead = (lead.get("original") or {}).get("source", "")
    pages = call(action="query", titles=title, generator="images", gimlimit=30,
                 prop="imageinfo", iiprop="url|size|mime").get("query", {}).get("pages", [])
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/gif"):
            continue
        name = p["title"].split(":", 1)[-1]
        url = info["url"].split("?")[0]  # drop Wikimedia's tracking parameters
        out.append((name, f"{info.get('width')}x{info.get('height')}", url))
    out.sort(key=lambda t: (t[2] != lead.split("?")[0], _rank(t[0])))
    return title, out


def find_cover(game: str, limit: int = 12) -> str:
    lines = []
    try:
        page, imgs = _c64wiki(game)
        if page:
            lines.append(f"C64-Wiki page {page!r} (C64 screenshots convert best):")
            lines += [f"  {_clean(n)}  {u}" for n, u in imgs[:limit]] or ["  (no pictures)"]
        else:
            lines.append("C64-Wiki: no page under that name - try the exact game title")
    except Exception as exc:
        lines.append(f"C64-Wiki: failed ({exc})")
    try:
        article, imgs = _wikipedia(game)
        if article:
            lines.append(f"Wikipedia article {article!r} (the first is the infobox image, usually the box art):")
            lines += [f"  {_clean(n)}  {s}  {u}" for n, s, u in imgs[:limit]] or ["  (no pictures)"]
        else:
            lines.append("Wikipedia: no article found")
    except Exception as exc:
        lines.append(f"Wikipedia: failed ({exc})")
    return "\n".join(lines)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]
    if not slug:
        raise MachineError("give the picture a name, e.g. 'paperboy'")
    return slug


def picture_files(name: str) -> tuple[Path, Path]:
    out = settings.DOWNLOAD_DIR / "pictures" / f"{_slug(name)}_screen.bin"
    return out, out.with_name(out.stem + "_preview.png")


def make_picture(url: str, name: str, crop: str = "", fill: bool = False) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    if urllib.parse.urlparse(url).scheme != "https" or host not in IMAGE_HOSTS:
        raise MachineError("only images find_cover listed (C64-Wiki or Wikimedia) can be used")
    if crop and not re.fullmatch(r"\d+,\d+,\d+,\d+", crop):
        raise MachineError("crop is X,Y,W,H in the image's own pixels, e.g. 0,0,571,365")
    out, preview = picture_files(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = _get(url, limit=MAX_IMAGE, timeout=60)
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".img"
    with tempfile.TemporaryDirectory() as d:  # its own empty folder: downloaded data
        src = Path(d) / ("cover" + ext)
        src.write_bytes(data)
        cmd = [sys.executable, "-I", str(CODE / "img2be6502.py"), str(src), "-o", str(out)]
        if crop:
            cmd += ["--crop", crop]
        if fill:
            cmd.append("--fill")
        r = subprocess.run(cmd, cwd=CODE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    text = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not out.exists():
        raise MachineError("conversion failed: " + text[-600:])
    text = "\n".join(l for l in text.splitlines() if not l.startswith(("wrote", "load it")))
    rel = machine._rel(out)
    return (f"{text}\nSaved as {rel} - use that as play_tune's picture, with a screen-safe tune.\n"
            "The user has been sent a preview of how it will look.")
