"""Canvas facts from original bytes; object shape and client metadata are not facts."""
from hashlib import sha256
from io import BytesIO
from math import gcd
from pathlib import Path


def ratio_for(width: int, height: int) -> str:
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def read_image_dimensions(path: Path, *, max_bytes=64 * 1024 * 1024) -> dict:
    from PIL import Image
    from services.file_resources import file_version
    before = file_version(path)
    if before[1] > max_bytes:
        raise ValueError("IMAGE_DIMENSIONS_UNAVAILABLE")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise ValueError("IMAGE_DIMENSIONS_UNAVAILABLE")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            orientation = image.getexif().get(274, 1)
        with Image.open(BytesIO(data)) as image:
            image.verify()
        if orientation in (5, 6, 7, 8):
            width, height = height, width
        if width <= 0 or height <= 0:
            raise ValueError("IMAGE_DIMENSIONS_UNAVAILABLE")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError("IMAGE_DIMENSIONS_UNAVAILABLE") from error
    if file_version(path) != before:
        raise ValueError("IMAGE_REFERENCE_CHANGED")
    return {"width": width, "height": height, "aspect_ratio": ratio_for(width, height),
            "content_sha256": sha256(data).hexdigest(), "file_version": list(before)}


def output_size_check(facts: dict, target: dict) -> dict:
    ratio = target.get("aspect_ratio")
    matches = True
    if ratio and ratio != "auto":
        width, height = map(int, ratio.split(":"))
        # Allow integer-pixel rounding, not a different canvas composition.
        difference = abs(facts["width"] * height - facts["height"] * width)
        scale = max(facts["width"] * height, facts["height"] * width)
        matches = difference <= max(width, height) and difference / scale <= 0.001
    if target.get("width") and target.get("height"):
        matches = matches and (facts["width"], facts["height"]) == (target["width"], target["height"])
    return {"target": target, "actual": {key: facts[key] for key in ("width", "height", "aspect_ratio")},
            "size_matches": matches}
