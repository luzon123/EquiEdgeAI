"""
Image validation and pre-processing utilities.

Responsibilities:
- Validate file extension and MIME type.
- Validate file size against a configurable limit.
- Detect and reject corrupted files.
- Optionally downscale oversized images while preserving quality.
- Return clean bytes ready for the vision provider.

Never reads from or writes to disk directly — operates on in-memory bytes
so there are no leftover temp files.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

from utils.logging_setup import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

# Maximum upload size in bytes (default: 20 MB)
MAX_IMAGE_BYTES: int = int(os.environ.get("VISION_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))

# Maximum pixel dimension before the image is resized (default: 2048 px)
MAX_IMAGE_DIMENSION: int = int(os.environ.get("VISION_MAX_IMAGE_DIMENSION", "2048"))

# Allowed MIME types and their canonical names
_ALLOWED_MIME: dict[str, str] = {
    "image/png":  "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg":  "image/jpeg",
}

# Allowed file extensions (lowercase, with dot)
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg"})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ProcessedImage:
    data:      bytes
    mime_type: str     # normalised, e.g. "image/png"
    width:     int
    height:    int
    was_resized: bool


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def validate_and_process_image(
    file_bytes: bytes,
    filename:   str,
    *,
    max_bytes:     int = MAX_IMAGE_BYTES,
    max_dimension: int = MAX_IMAGE_DIMENSION,
) -> ProcessedImage:
    """
    Validate and optionally resize an uploaded image.

    Args:
        file_bytes:    Raw bytes from the uploaded file.
        filename:      Original filename (used only for extension check).
        max_bytes:     Maximum allowed file size in bytes.
        max_dimension: If the image is wider or taller than this, resize it.

    Returns:
        ProcessedImage with clean bytes and metadata.

    Raises:
        ValueError: on any validation failure (bad format, too large, corrupt).
    """
    # -- Extension check -----------------------------------------------------
    ext = _get_extension(filename)
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext}'. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}."
        )

    # -- Size check (before decoding — cheap) --------------------------------
    size = len(file_bytes)
    if size == 0:
        raise ValueError("Uploaded file is empty.")
    if size > max_bytes:
        raise ValueError(
            f"Image is too large ({size / (1024*1024):.1f} MB). "
            f"Maximum allowed: {max_bytes / (1024*1024):.0f} MB."
        )

    # -- Decode with Pillow (catches corruption) ------------------------------
    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is not installed. Add 'Pillow>=10.0.0' to requirements.txt."
        ) from exc

    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()                # raises if file is corrupted/truncated
    except Exception as exc:
        raise ValueError(f"Image file is corrupted or unreadable: {exc}") from exc

    # Re-open after verify() (verify() closes the file handle)
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.load()                  # fully decode into memory
    except Exception as exc:
        raise ValueError(f"Image could not be decoded: {exc}") from exc

    # -- MIME type from actual image content ---------------------------------
    fmt = (img.format or "").upper()
    mime_map = {"PNG": "image/png", "JPEG": "image/jpeg", "JPG": "image/jpeg"}
    mime_type = mime_map.get(fmt)
    if mime_type is None:
        raise ValueError(
            f"Unsupported image format '{fmt}'. "
            f"Only PNG and JPEG are accepted."
        )

    original_w, original_h = img.size
    was_resized = False

    # -- Resize if needed -----------------------------------------------------
    if original_w > max_dimension or original_h > max_dimension:
        img, was_resized = _resize_image(img, max_dimension)
        logger.info(
            "Image resized from %dx%d to %dx%d.",
            original_w, original_h, img.size[0], img.size[1],
        )

    # -- Re-encode to bytes --------------------------------------------------
    output = io.BytesIO()
    save_fmt = "PNG" if mime_type == "image/png" else "JPEG"
    save_kwargs: dict = {}
    if save_fmt == "JPEG":
        save_kwargs = {"quality": 92, "optimize": True}
        # Ensure no alpha channel for JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

    img.save(output, format=save_fmt, **save_kwargs)
    processed_bytes = output.getvalue()

    logger.debug(
        "Image processed | original=%d bytes → processed=%d bytes | "
        "size=%dx%d | mime=%s | resized=%s",
        size, len(processed_bytes), img.size[0], img.size[1], mime_type, was_resized,
    )

    return ProcessedImage(
        data        = processed_bytes,
        mime_type   = mime_type,
        width       = img.size[0],
        height      = img.size[1],
        was_resized = was_resized,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_extension(filename: str) -> str:
    """Return the lowercase file extension including the dot."""
    if not filename:
        return ""
    parts = filename.rsplit(".", 1)
    if len(parts) < 2:
        return ""
    return "." + parts[1].lower()


def _resize_image(img: "Image.Image", max_dim: int) -> tuple["Image.Image", bool]:
    """
    Resize image so neither dimension exceeds max_dim.

    Uses LANCZOS resampling for quality.  Preserves aspect ratio.
    Returns (resized_image, was_resized).
    """
    from PIL import Image  # type: ignore[import]

    w, h = img.size
    if w <= max_dim and h <= max_dim:
        return img, False

    scale = min(max_dim / w, max_dim / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return resized, True
