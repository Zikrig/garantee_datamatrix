import io

from PIL import Image, ImageFilter, ImageOps
from pylibdmtx.pylibdmtx import decode

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional fallback for local runs
    cv2 = None
    np = None


DECODE_PARAM_SETS = [
    {"timeout": 200},
    {"timeout": 200, "threshold": 5},
    {"timeout": 200, "threshold": 10},
    {"timeout": 200, "sharpen": 1},
    {"timeout": 200, "sharpen": 2},
    {"timeout": 200, "min_edge": 10, "max_edge": 512},
]


def _pil_variants(image: Image.Image) -> list[Image.Image]:
    variants: list[Image.Image] = []
    base = ImageOps.exif_transpose(image)
    gray = ImageOps.grayscale(base)

    for angle in (0, 90, 180, 270):
        rotated = gray.rotate(angle, expand=True) if angle else gray
        variants.extend(
            [
                rotated,
                ImageOps.autocontrast(rotated),
                ImageOps.equalize(rotated),
                ImageOps.invert(rotated),
                rotated.filter(ImageFilter.SHARPEN),
                rotated.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3)),
            ]
        )

        for scale in (2, 3):
            resized = rotated.resize(
                (rotated.width * scale, rotated.height * scale), Image.BICUBIC
            )
            variants.append(resized)
            variants.append(ImageOps.autocontrast(resized))
    return variants


def _cv_variants(image: Image.Image) -> list[Image.Image]:
    if cv2 is None or np is None:
        return []

    base = ImageOps.exif_transpose(image)
    if base.mode != "L":
        base = base.convert("L")
    gray = np.array(base)
    variants: list[Image.Image] = []

    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(Image.fromarray(otsu))
    variants.append(Image.fromarray(cv2.bitwise_not(otsu)))

    adapt = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
    )
    variants.append(Image.fromarray(adapt))

    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, otsu_blur = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(Image.fromarray(otsu_blur))

    for scale in (2, 3):
        scaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        variants.append(Image.fromarray(scaled))
        _, otsu_scaled = cv2.threshold(
            scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        variants.append(Image.fromarray(otsu_scaled))

    return variants


def extract_datamatrix(image_bytes: bytes) -> list[str]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        variants = _pil_variants(image) + _cv_variants(image)

    found: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        for params in DECODE_PARAM_SETS:
            try:
                results = decode(variant, **params)
            except Exception:
                continue
            for item in results:
                value = item.data.decode("utf-8", errors="replace")
                if value not in seen:
                    seen.add(value)
                    found.append(value)
        if found:
            break

    return found






















