"""The damage photos: simple drawings (decision D35), one scene each.

Fixed seed for the noise, a slight blur, no EXIF: the same bytes on every run
on one machine (the JPEG codec may differ between machines).
"""

import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

PHOTO_SIZE = (800, 600)


def finish(img: Image.Image, path: Path, seed: int) -> None:
    """Blur, add grey noise and save as JPEG (no `exif` argument: none is written)."""
    w, h = PHOTO_SIZE
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    noise = Image.frombytes("L", PHOTO_SIZE, random.Random(seed).randbytes(w * h))
    noise = noise.point(lambda v: 118 + v // 10).convert("RGB")  # grey around 118..143
    img = ImageChops.add(img, noise, scale=1.0, offset=-130)
    img.save(path, "JPEG", quality=88)


def description(text: str, box: tuple[float, float, float, float]) -> dict[str, Any]:
    return {
        "document_type": "damage_photo",
        "fields": [
            {
                "name": "damage_description",
                "value": text,
                "confidence": 1.0,
                "page": 1,
                "box": [float(v) for v in box],
            }
        ],
        "line_items": [],
    }


def kitchen_photo(
    path: Path, seed: int, stain: tuple[int, int, int, int]
) -> dict[str, Any]:
    """A rough kitchen: wall, cabinets, tiled floor and a water stain at `stain`."""
    w, h = PHOTO_SIZE
    img = Image.new("RGB", PHOTO_SIZE, (214, 208, 196))  # wall
    g = ImageDraw.Draw(img)
    floor_y = 440
    g.rectangle((0, floor_y, w, h), fill=(168, 150, 128))  # floor
    for x in range(-200, w + 200, 90):  # floor tile joints, in perspective
        g.line(
            (x + (x - w // 2) // 3, h, x + (x - w // 2) // 8, floor_y),
            fill=(140, 124, 104),
            width=2,
        )
    for y in (480, 535):
        g.line((0, y, w, y), fill=(140, 124, 104), width=2)
    g.rectangle((60, 90, 740, 110), fill=(120, 100, 80))  # worktop
    g.rectangle((60, 110, 740, floor_y - 20), fill=(186, 160, 120))  # lower cabinets
    for x0 in (60, 290, 520):  # doors and handles
        g.rectangle(
            (x0 + 6, 120, x0 + 214, floor_y - 30), outline=(120, 100, 76), width=3
        )
        g.rectangle((x0 + 170, 200, x0 + 178, 250), fill=(90, 90, 90))
    g.rectangle((60, floor_y - 20, 740, floor_y), fill=(90, 74, 58))  # plinth
    # the water damage: a dark stain on the floor, a damp band at the cabinet foot
    x0, y0, x1, y1 = stain
    g.ellipse((x0, y0, x1, y1), fill=(112, 92, 72))
    g.ellipse((x0 + 25, y0 + 12, x1 - 25, y1 - 12), fill=(92, 74, 58))
    for i in range(70):  # damp cabinet foot: dark at the floor, fading upwards
        t = i / 70
        rgb = tuple(
            int(a + (b - a) * t)
            for a, b in zip((128, 102, 74), (186, 160, 120), strict=True)
        )
        g.line((x0 + 20, floor_y - 21 - i, x1 - 20, floor_y - 21 - i), fill=rgb)
    finish(img, path, seed)
    return description(
        "water stain on the kitchen floor and the lower cabinets",
        (x0, floor_y - 70, x1, y1),
    )


def door_photo(path: Path) -> dict[str, Any]:
    """A front door forced at the lock: splintered frame, pry marks, wood chips."""
    img = Image.new("RGB", PHOTO_SIZE, (206, 202, 194))  # wall
    g = ImageDraw.Draw(img)
    g.rectangle((0, 480, 800, 600), fill=(150, 146, 140))  # stone floor
    for x in range(0, 800, 100):
        g.line((x, 480, x - 40, 600), fill=(122, 118, 112), width=2)
    g.rectangle((0, 480, 800, 486), fill=(110, 106, 100))
    g.rectangle((250, 40, 550, 480), fill=(92, 68, 48))  # frame
    g.rectangle((262, 52, 538, 480), fill=(150, 112, 78))  # door leaf
    for box in ((284, 80, 516, 250), (284, 280, 516, 450)):  # panels
        g.rectangle(box, outline=(112, 82, 56), width=4)
    g.ellipse((500, 262, 520, 282), fill=(70, 70, 72))  # handle rose
    g.rectangle((470, 268, 505, 276), fill=(70, 70, 72))  # lever
    g.rectangle((508, 300, 524, 322), fill=(80, 80, 84))  # cylinder
    # the forcing: a gap at the frame, a splintered edge, pry marks, chips
    g.polygon(
        [
            (538, 230),
            (552, 236),
            (546, 270),
            (556, 300),
            (540, 330),
            (544, 360),
            (534, 350),
            (538, 300),
        ],
        fill=(214, 186, 144),
    )
    g.polygon(
        [(536, 238), (548, 240), (544, 262), (552, 296), (538, 322), (534, 300)],
        fill=(40, 30, 24),
    )
    for dy in (0, 14, 28):  # tool marks in the leaf
        g.line((488, 296 + dy, 512, 284 + dy), fill=(70, 50, 34), width=3)
    g.polygon([(520, 322), (532, 336), (516, 344)], fill=(214, 186, 144))
    for x, y in ((560, 492), (590, 510), (540, 520), (610, 540), (575, 560)):
        g.polygon([(x, y), (x + 22, y + 4), (x + 8, y + 10)], fill=(214, 186, 144))
    finish(img, path, 3)
    return description(
        "front door forced open at the lock, frame splintered, pry marks on the leaf",
        (460, 220, 570, 370),
    )


def floor_photo(path: Path) -> dict[str, Any]:
    """A bathroom floor with a wide water stain and a damp wall foot."""
    img = Image.new("RGB", PHOTO_SIZE, (196, 210, 216))  # wall tiles
    g = ImageDraw.Draw(img)
    for x in range(0, 800, 50):
        g.line((x, 0, x, 400), fill=(160, 176, 184), width=2)
    for y in range(0, 400, 50):
        g.line((0, y, 800, y), fill=(160, 176, 184), width=2)
    g.rectangle((0, 400, 800, 600), fill=(176, 164, 148))  # floor tiles
    for x in range(-300, 1100, 100):
        g.line(
            (x + (x - 400) // 3, 600, x + (x - 400) // 8, 400),
            fill=(142, 130, 116),
            width=2,
        )
    for y in (440, 490, 555):
        g.line((0, y, 800, y), fill=(142, 130, 116), width=2)
    g.rectangle((0, 392, 800, 404), fill=(120, 130, 134))  # skirting
    g.ellipse((90, 450, 640, 590), fill=(118, 106, 92))  # the stain
    g.ellipse((150, 470, 560, 575), fill=(98, 88, 76))
    g.ellipse((230, 485, 430, 530), fill=(150, 160, 164))  # standing water
    for i in range(60):  # damp wall foot: dark at the floor, fading upwards
        t = i / 60
        rgb = tuple(
            int(a + (b - a) * t)
            for a, b in zip((120, 134, 140), (196, 210, 216), strict=True)
        )
        g.line((100, 391 - i, 620, 391 - i), fill=rgb)
    g.rectangle((690, 330, 712, 392), fill=(176, 178, 182))  # supply pipe, burst
    g.polygon([(684, 330), (718, 330), (706, 316), (696, 316)], fill=(140, 142, 146))
    finish(img, path, 4)
    return description(
        "water stain on the bathroom floor and the foot of the tiled wall",
        (90, 330, 640, 590),
    )
