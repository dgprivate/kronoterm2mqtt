#!/usr/bin/env python3
"""Draw the add-on's icon and logo.

Home Assistant shows an icon in the add-on list and a logo on the add-on's own page.
Both are pixel art: a 32x32 grid of a heat pump - the outdoor unit with its fan, and the
heat coming off it - scaled up with no smoothing, so it stays crisp at any size the store
happens to use.

The art is generated rather than drawn in an editor so it can be changed by editing a few
numbers here instead of by finding the original file years later. Nothing outside the
standard library is needed:

    python3 ha-addon/artwork.py

Writes ha-addon/icon.png (256x256) and ha-addon/logo.png (240x96).
"""

import math
from pathlib import Path
import struct
import zlib


HERE = Path(__file__).parent

TRANSPARENT = (0, 0, 0, 0)
BACKGROUND = (18, 49, 61, 255)  # Deep teal, dark enough for white text on top
BODY = (233, 243, 246, 255)
SHADE = (150, 185, 198, 255)
OUTLINE = (10, 28, 36, 255)
FAN = (28, 111, 140, 255)
FAN_LIGHT = (127, 212, 232, 255)
HEAT = (242, 98, 46, 255)

# A 4x5 pixel font, only the letters the wordmark needs.
GLYPHS = {
    'K': ('#  #', '# # ', '##  ', '# # ', '#  #'),
    'R': ('### ', '#  #', '### ', '# # ', '#  #'),
    'O': (' ## ', '#  #', '#  #', '#  #', ' ## '),
    'N': ('#  #', '## #', '# ##', '#  #', '#  #'),
    'T': ('####', ' ## ', ' ## ', ' ## ', ' ## '),
    'E': ('####', '#   ', '### ', '#   ', '####'),
    'M': ('#  #', '####', '####', '#  #', '#  #'),
    'Q': (' ## ', '#  #', '#  #', ' ## ', '   #'),
    '2': (' ## ', '#  #', '  # ', ' #  ', '####'),
}


class Canvas:
    def __init__(self, width: int, height: int, fill=TRANSPARENT):
        self.width = width
        self.height = height
        self.pixels = [[fill] * width for _ in range(height)]

    def set(self, x: int, y: int, colour) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y][x] = colour

    def rect(self, x0: int, y0: int, x1: int, y1: int, colour) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.set(x, y, colour)

    def rounded_rect(self, x0: int, y0: int, x1: int, y1: int, radius: int, colour) -> None:
        """A rectangle with the corner pixels outside the radius left alone."""
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                dx = max(x0 + radius - x, x - (x1 - radius), 0)
                dy = max(y0 + radius - y, y - (y1 - radius), 0)
                if dx * dx + dy * dy <= radius * radius:
                    self.set(x, y, colour)

    def ring(self, cx: float, cy: float, radius: float, thickness: float, colour) -> None:
        for y in range(self.height):
            for x in range(self.width):
                distance = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                if radius - thickness <= distance <= radius:
                    self.set(x, y, colour)

    def disc(self, cx: float, cy: float, radius: float, colour) -> None:
        self.ring(cx, cy, radius, radius, colour)

    def squiggle(self, x: int, y0: int, height: int, colour, cycles: float = 1.5) -> None:
        """A wavy vertical line, one pixel wide and unbroken.

        Rounding the wave to whole pixels makes it step sideways; without filling the
        step the line reads as a column of dashes rather than as something rising.
        """
        previous = None
        for row in range(height):
            offset = round(math.sin(row / height * cycles * 2 * math.pi))
            if previous is not None:
                for between in range(min(previous, offset), max(previous, offset) + 1):
                    self.set(x + between, y0 + row, colour)
            self.set(x + offset, y0 + row, colour)
            previous = offset

    def spoke(self, cx: float, cy: float, degrees: float, length: float, colour) -> None:
        """A fan blade: a short thick line from the hub outwards."""
        dx = math.cos(math.radians(degrees))
        dy = -math.sin(math.radians(degrees))
        for step in range(int(length * 4) + 1):
            distance = step / 4
            self.set(round(cx + dx * distance), round(cy + dy * distance), colour)
            self.set(round(cx + dx * distance) + 1, round(cy + dy * distance), colour)

    def scaled(self, factor: int) -> 'Canvas':
        """Nearest neighbour, because the point is to see the pixels."""
        bigger = Canvas(self.width * factor, self.height * factor)
        for y in range(self.height):
            for x in range(self.width):
                bigger.rect(x * factor, y * factor, (x + 1) * factor - 1, (y + 1) * factor - 1, self.pixels[y][x])
        return bigger

    def paste(self, other: 'Canvas', x0: int, y0: int) -> None:
        for y in range(other.height):
            for x in range(other.width):
                colour = other.pixels[y][x]
                if colour[3]:
                    self.set(x0 + x, y0 + y, colour)

    def write_text(self, text: str, x0: int, y0: int, scale: int, colour) -> None:
        for index, character in enumerate(text):
            glyph = GLYPHS[character]
            left = x0 + index * 5 * scale
            for row, line in enumerate(glyph):
                for column, mark in enumerate(line):
                    if mark == '#':
                        self.rect(
                            left + column * scale,
                            y0 + row * scale,
                            left + (column + 1) * scale - 1,
                            y0 + (row + 1) * scale - 1,
                            colour,
                        )

    def to_png(self) -> bytes:
        raw = b''.join(b'\x00' + bytes(value for pixel in row for value in pixel) for row in self.pixels)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)

        header = struct.pack('>IIBBBBB', self.width, self.height, 8, 6, 0, 0, 0)
        return (
            b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
        )


def heat_pump(background=BACKGROUND) -> Canvas:
    """The mark: an outdoor unit seen from the front, with heat rising off it."""
    canvas = Canvas(32, 32)
    canvas.rounded_rect(0, 0, 31, 31, 6, background)

    # Heat rising: three squiggles waving their way up
    for x in (9, 16, 23):
        canvas.squiggle(x, 2, 10, HEAT)

    # The unit, and the feet it stands on
    canvas.rect(4, 12, 27, 27, OUTLINE)
    canvas.rect(5, 13, 26, 26, BODY)
    canvas.rect(7, 28, 9, 29, OUTLINE)
    canvas.rect(22, 28, 24, 29, OUTLINE)

    # The fan: a ring, three blades and a hub
    canvas.ring(11.5, 19.5, 6, 1.5, FAN)
    canvas.disc(11.5, 19.5, 4.5, FAN_LIGHT)
    for degrees in (90, 210, 330):
        canvas.spoke(11.5, 19.5, degrees, 4.5, FAN)
    canvas.disc(11.5, 19.5, 1.5, OUTLINE)

    # The grille on the other half
    for x in (20, 22, 24):
        canvas.rect(x, 15, x, 24, SHADE)

    return canvas


def icon() -> Canvas:
    """The add-on list: a square mark, 256x256."""
    return heat_pump().scaled(8)


def logo() -> Canvas:
    """The add-on page: the mark and the name, within the 250x100 the store allows."""
    canvas = Canvas(240, 96)
    canvas.rounded_rect(0, 0, 239, 95, 12, BACKGROUND)
    canvas.paste(heat_pump().scaled(2), 10, 16)
    canvas.write_text('KRONOTERM', 86, 25, 3, BODY)
    canvas.write_text('2MQTT', 86, 48, 3, FAN_LIGHT)
    return canvas


def main() -> None:
    for name, canvas in (('icon.png', icon()), ('logo.png', logo())):
        (HERE / name).write_bytes(canvas.to_png())
        print(f'Wrote {HERE / name}')


if __name__ == '__main__':
    main()
