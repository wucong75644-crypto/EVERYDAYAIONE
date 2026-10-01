"""Optional image output sizing, independent of model generation and billing."""

from io import BytesIO


def resize_taobao_main_image(content: bytes) -> tuple[bytes, str]:
    """Always resize the selected output to 1440; callers own retry/fallback."""
    from PIL import Image, ImageOps

    with Image.open(BytesIO(content)) as source:
        image_format = source.format or "PNG"
        mime = Image.MIME[image_format]
        image = ImageOps.exif_transpose(source)
        if image.mode == "P":
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        if image_format == "JPEG" and image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
        resized = image.resize((1440, 1440), Image.Resampling.LANCZOS)
        options = {"icc_profile": source.info["icc_profile"]} if source.info.get("icc_profile") else {}
        if image_format == "JPEG":
            options.update(quality=95, subsampling=0)
        output = BytesIO()
        resized.save(output, format=image_format, **options)
        return output.getvalue(), mime
