"""Hard versions of a kit PDF for the reader: a flat scan and a phone photo.

Same content, other bytes. The page is rendered with pypdfium2 (150 DPI) and
changed with Pillow; every random part has a fixed seed, so two runs on one machine
give the same bytes. A scan is a one-page PDF that holds only a grey image: it has
no text layer. A phone photo is a JPEG of the page on a table, tilted, unevenly lit.
The image codec and the font substitution of pdfium may differ between machines: the
tests compare the pixels with a tolerance, as they do for the damage photos.
"""

import io
import random
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

DPI = 150


def render(pdf: Path) -> Image.Image:
    """Page 1 of a PDF as an RGB image at 150 DPI."""
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return doc[0].render(scale=DPI / 72).to_pil().convert("RGB")
    finally:
        doc.close()


def _noise(size: tuple[int, int], seed: int, amplitude: int) -> Image.Image:
    """Grey noise around 128, to be added with offset -128."""
    raw = random.Random(seed).randbytes(size[0] * size[1])
    return Image.frombytes("L", size, raw).point(
        lambda v: 128 - amplitude + v * 2 * amplitude // 255
    )


def scan(pdf: Path, out: Path, seed: int, angle: float) -> None:
    """A flat-bed scan: grey, turned by `angle` degrees, a little blur and noise."""
    page = render(pdf).convert("L").point(lambda v: v * 94 // 100)  # grey paper
    page = page.rotate(angle, Image.Resampling.BICUBIC, fillcolor=226)  # scanner lid
    page = page.filter(ImageFilter.GaussianBlur(0.9))
    page = ImageChops.add(page, _noise(page.size, seed, 7), scale=1.0, offset=-128)
    jpeg = io.BytesIO()
    page.save(jpeg, "JPEG", quality=68)
    jpeg.seek(0)
    c = Canvas(str(out), pagesize=A4, invariant=1, pageCompression=0)
    c.setTitle("Scan")
    c.setAuthor("")
    c.drawImage(ImageReader(jpeg), 0, 0, A4[0], A4[1])
    c.showPage()
    c.save()


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss elimination with a pivot: the 8 unknowns of a perspective transform."""
    n = len(b)
    m = [row[:] + [v] for row, v in zip(a, b, strict=True)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        for r in range(n):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [x - f * y for x, y in zip(m[r], m[i], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def perspective(
    dst: list[tuple[float, float]], src: list[tuple[float, float]]
) -> list[float]:
    """The 8 coefficients Pillow wants: output point (dst) -> input point (src)."""
    a: list[list[float]] = []
    b: list[float] = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    return _solve(a, b)


def _table(size: tuple[int, int], seed: int) -> Image.Image:
    """A wooden table: brown, with grain lines."""
    img = Image.new("RGB", size, (112, 84, 60))
    g = ImageDraw.Draw(img)
    rng = random.Random(seed)
    for _ in range(260):
        y = rng.randrange(size[1])
        shade = rng.randrange(-16, 12)
        g.line(
            (0, y, size[0], y + rng.randrange(-6, 7)),
            fill=(112 + shade, 84 + shade, 60 + shade),
            width=rng.randrange(1, 4),
        )
    return img


def photo(pdf: Path, out: Path, seed: int) -> None:
    """A phone photo of the page on a table: tilted, uneven light, JPEG, no EXIF."""
    page = render(pdf)
    flat = _table((1800, 2300), seed)
    shadow = Image.new("L", flat.size, 255)
    ImageDraw.Draw(shadow).rectangle(
        (300, 300, 300 + page.width, 300 + page.height), fill=150
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    flat = ImageChops.multiply(flat, shadow.convert("RGB"))
    flat.paste(page, (280, 270))
    # the camera looks at the page from below and a little to the left
    size = (1200, 1600)
    corners = [(0, 0), (size[0], 0), (size[0], size[1]), (0, size[1])]
    quad = [(30, 100), (1770, 60), (1640, 2200), (160, 2240)]
    img = flat.transform(
        size,
        Image.Transform.PERSPECTIVE,
        perspective(corners, quad),
        Image.Resampling.BICUBIC,
    )
    # uneven light: bright off-centre, darker on one side
    w, h = size
    radial = Image.radial_gradient("L").resize((2 * w, 2 * h))
    radial = radial.crop((int(w * 0.4), int(h * 0.2), int(w * 1.4), int(h * 1.2)))
    light = ImageChops.multiply(
        radial.point(lambda v: 255 - v * 36 // 100).convert("RGB"),
        Image.linear_gradient("L")
        .transpose(Image.Transpose.ROTATE_90)
        .resize(size)
        .point(lambda v: 232 + v // 11)
        .convert("RGB"),
    )
    img = ImageChops.multiply(img, light)
    img = img.filter(ImageFilter.GaussianBlur(0.9))
    img = ImageChops.add(
        img, _noise(size, seed, 5).convert("RGB"), scale=1.0, offset=-128
    )
    img.save(out, "JPEG", quality=85)
