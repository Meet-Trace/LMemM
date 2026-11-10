"""LMemM - a film-grain tile, made once in code (no picture files).

A square of fine, unclumped noise as a PNG (grey on a transparent ground, so it sits on any background).
The window tiles it at a low opacity behind the pages. The noise is seeded, so it is the same grain every
launch and is never regenerated while the app runs."""

import random
import struct
import zlib

SIZE = 192                 # pixels; drawn at half that size in points, so it stays fine on a Retina screen


def tile_png(opacity=0.05, size=SIZE, seed=7):
    """PNG bytes of a size x size grey-and-alpha tile. `opacity` is the average strength (0..1)."""
    rng = random.Random(seed)
    top = max(1, min(255, int(255 * opacity * 2)))        # alpha is spread 0..top, so the mean is `opacity`
    rows = bytearray()
    for _ in range(size):
        rows.append(0)                                    # filter: none
        for _ in range(size):
            grey = 255 if rng.random() < 0.5 else 0       # light and dark specks in equal measure: no tint
            rows.append(grey)
            rows.append(int(rng.random() * top))
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", size, size, 8, 4, 0, 0, 0)         # 8-bit grey + alpha
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")
