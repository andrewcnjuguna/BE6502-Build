r"""
Turn a picture - a C64 screenshot, say - into a screen for the Worlds
Worst Video Card on this machine.

    python3 img2be6502.py rambo.png                 writes rambo_screen.bin
    python3 be6502.py load rambo_screen.bin 2000    and shows it

The display is RAM at $2000-$3FFF: 64 rows of 128 bytes, the first 100
of each row visible, one byte per pixel as BBGGGRRR - blue in the top
two bits, red in the bottom three. (The docs said RRRGGGBB; a test card
on the real screen showed red as blue and blue as dark red.) The first
four or five columns show as noise on this card, so nothing important
should sit at the left edge. The output is always 8192 bytes for $2000,
with columns 100-127 left black.

A C64 screen is 320x200, which scales to 100x62 - it fills this display
almost exactly. A screenshot's border, if it has one, is found from the
corners and cropped first. A picture with 16 colours or fewer - a real
C64 screenshot - has each pixel snapped to the C64's own palette before
going to the card's colours, which keeps the look of the original instead of a
mush of in-between shades. Anything richer, a photo or box art, goes
straight to the card's 256 colours: through the C64 palette, the red
RAMBO logo on the box came out brown. --c64 / --no-c64 force either.

A portrait picture, like a box cover, is best cropped to the part that
suits a wide screen with --crop X,Y,W,H - the front of Rambo II's box
from the top down to his chest is --crop 0,0,571,365.

A preview PNG, 8x size, is written beside the .bin, so it can be checked
before anything is sent.

Needs Pillow:  python3 -m pip install pillow

Anything else in $2000-$3FFF gets overwritten, and a tune that loads
there overwrites the picture - check the tune's load range first.
"""
import argparse
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("needs Pillow:  python3 -m pip install pillow")

WIDTH, HEIGHT, STRIDE = 100, 64, 128
SCREEN = 0x2000

# The C64 palette, Pepto's measurements
C64 = [(0x00, 0x00, 0x00), (0xFF, 0xFF, 0xFF), (0x68, 0x37, 0x2B), (0x70, 0xA4, 0xB2),
       (0x6F, 0x3D, 0x86), (0x58, 0x8D, 0x43), (0x35, 0x28, 0x79), (0xB8, 0xC7, 0x6F),
       (0x6F, 0x4F, 0x25), (0x43, 0x39, 0x00), (0x9A, 0x67, 0x59), (0x44, 0x44, 0x44),
       (0x6C, 0x6C, 0x6C), (0x9A, 0xD2, 0x84), (0x6C, 0x5E, 0xB5), (0x95, 0x95, 0x95)]


def nearest(rgb, palette):
    return min(palette, key=lambda p: sum((a - b) ** 2 for a, b in zip(rgb, p)))


def to_byte(rgb):
    """BBGGGRRR, as the card is wired."""
    r, g, b = rgb
    return (round(b * 3 / 255) << 6) | (round(g * 7 / 255) << 3) | round(r * 7 / 255)


def from_byte(v):
    """What the card shows for a byte, for the preview."""
    return (round((v & 7) * 255 / 7), round((v >> 3 & 7) * 255 / 7), round((v >> 6) * 255 / 3))


def crop_border(img):
    """Crop rows and columns that are all the corner colour, if all four
    corners agree - a C64 screenshot with its border on."""
    w, h = img.size
    px = img.load()
    corner = px[0, 0]
    if any(px[x, y] != corner for x, y in ((w - 1, 0), (0, h - 1), (w - 1, h - 1))):
        return img, None
    rows = [y for y in range(h) if any(px[x, y] != corner for x in range(w))]
    cols = [x for x in range(w) if any(px[x, y] != corner for y in range(h))]
    if not rows or not cols:
        return img, None
    return img.crop((cols[0], rows[0], cols[-1] + 1, rows[-1] + 1)), corner


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("-o", "--out", help="output .bin (default <image>_screen.bin)")
    ap.add_argument("--c64", dest="c64", action="store_true", default=None,
                    help="snap to the C64 palette even for a picture with many colours")
    ap.add_argument("--no-c64", dest="c64", action="store_false",
                    help="map straight to the card's colours even for a 16-colour picture")
    ap.add_argument("--keep-border", action="store_true", help="do not crop a uniform border")
    ap.add_argument("--crop", metavar="X,Y,W,H",
                    help="use only this part of the picture, in its own pixels - "
                         "e.g. the top of a portrait box cover")
    ap.add_argument("--fill", action="store_true",
                    help="crop to fill all 100x64 instead of fitting the whole picture")
    args = ap.parse_args()

    img = Image.open(args.image).convert("RGB")
    print("%s: %dx%d" % (os.path.basename(args.image), img.width, img.height))
    border = None
    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        img = img.crop((x, y, x + w, y + h))
        print("cropped to %dx%d at %d,%d" % (w, h, x, y))
    if not args.keep_border and not args.crop:
        img, border = crop_border(img)
        if border:
            print("border cropped, %dx%d left" % img.size)

    if args.c64 is None:
        args.c64 = len(img.getcolors(maxcolors=17) or range(17)) <= 16
    print("colours: %s" % ("through the C64 palette" if args.c64 else "straight to the card's 256"))

    scale = (max if args.fill else min)(WIDTH / img.width, HEIGHT / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    small = img.resize((w, h), Image.BOX)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), border or (0, 0, 0))
    canvas.paste(small, ((WIDTH - w) // 2, (HEIGHT - h) // 2))
    print("scaled to %dx%d, centred on %dx%d" % (min(w, WIDTH), min(h, HEIGHT), WIDTH, HEIGHT))

    px = canvas.load()
    screen = bytearray(STRIDE * HEIGHT)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            rgb = nearest(px[x, y], C64) if args.c64 else px[x, y]
            screen[y * STRIDE + x] = to_byte(rgb)

    out = args.out or os.path.splitext(args.image)[0] + "_screen.bin"
    with open(out, "wb") as f:
        f.write(screen)
    preview = Image.new("RGB", (WIDTH, HEIGHT))
    preview.putdata([from_byte(screen[y * STRIDE + x]) for y in range(HEIGHT) for x in range(WIDTH)])
    pv = os.path.splitext(out)[0] + "_preview.png"
    preview.resize((WIDTH * 8, HEIGHT * 8), Image.NEAREST).save(pv)
    print("wrote %s (%d bytes for $%04X) and %s" % (out, len(screen), SCREEN, os.path.basename(pv)))
    print("load it:  python3 be6502.py load %s %X" % (os.path.basename(out), SCREEN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
