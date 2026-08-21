"""
Tests for utils/image_utils.py — specifically the 2026-08-21 change that
skips the redundant verify()+re-open for JPEG (proven a no-op for that
format) while preserving PNG's real chunk-CRC verify() unchanged, plus the
existing already-within-target-dimensions fast path.

Run with:
    python -m unittest tests.test_image_utils -v
"""
from __future__ import annotations

import io
import unittest

from PIL import Image

from utils.image_utils import validate_and_process_image


def _jpeg_bytes(size, quality=90) -> bytes:
    img = Image.new("RGB", size, color=(40, 90, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _png_bytes(size) -> bytes:
    img = Image.new("RGB", size, color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ImageUtilsTests(unittest.TestCase):
    # -- Case: already-target-sized JPEG (942x2048) -> fast path, no resize ----
    def test_942x2048_jpeg_already_within_target(self):
        data = _jpeg_bytes((942, 2048))
        result = validate_and_process_image(file_bytes=data, filename="table.jpg")
        self.assertEqual((result.width, result.height), (942, 2048))
        self.assertFalse(result.was_resized)
        self.assertEqual(result.mime_type, "image/jpeg")
        # Fast path: original bytes returned unchanged, no re-encode.
        self.assertEqual(result.data, data)

    # -- Case: oversized JPEG (1206x2622) -> must resize+re-encode -------------
    def test_1206x2622_jpeg_oversized_gets_resized(self):
        data = _jpeg_bytes((1206, 2622))
        result = validate_and_process_image(file_bytes=data, filename="table.jpg")
        self.assertTrue(result.was_resized)
        self.assertLessEqual(result.width, 2048)
        self.assertLessEqual(result.height, 2048)
        self.assertEqual(result.mime_type, "image/jpeg")
        # Re-encoded (resize forces re-encode) - bytes differ from input.
        self.assertNotEqual(result.data, data)

    # -- Case: oversized PNG (1290x2796) -> resize path, PNG verify preserved --
    def test_1290x2796_png_oversized_gets_resized(self):
        data = _png_bytes((1290, 2796))
        result = validate_and_process_image(file_bytes=data, filename="table.png")
        self.assertTrue(result.was_resized)
        self.assertLessEqual(result.width, 2048)
        self.assertLessEqual(result.height, 2048)
        self.assertEqual(result.mime_type, "image/png")

    # -- Case: already-target-sized PNG -> fast path -----------------------------
    def test_already_target_sized_png_fast_path(self):
        data = _png_bytes((900, 1600))
        result = validate_and_process_image(file_bytes=data, filename="table.png")
        self.assertFalse(result.was_resized)
        self.assertEqual(result.data, data)
        self.assertEqual(result.mime_type, "image/png")

    # -- Case: malformed JPEG (bad extension/content mismatch, not an image) ---
    def test_malformed_jpeg_rejected(self):
        with self.assertRaises(ValueError):
            validate_and_process_image(file_bytes=b"not a real jpeg at all", filename="table.jpg")

    # -- Case: corrupted JPEG (valid header, truncated/garbage body) -----------
    # Proves the JPEG fast path (open + load, no verify()) still catches
    # corruption via load()'s own decode failure - the exact concern behind
    # dropping verify() for JPEG.
    def test_corrupted_jpeg_truncated_body_rejected(self):
        good = _jpeg_bytes((200, 200))
        # Keep the JPEG header (SOI/APP/SOF/DHT/SOS markers) but truncate
        # deep into the entropy-coded scan data - a valid-looking header
        # with a broken body, exactly the case verify() (for JPEG) would
        # NOT have caught, and load() must.
        truncated = good[: len(good) // 2]
        with self.assertRaises(ValueError) as ctx:
            validate_and_process_image(file_bytes=truncated, filename="table.jpg")
        self.assertIn("decoded", str(ctx.exception).lower())

    # -- Case: unsupported file (not an image format we accept) ----------------
    def test_unsupported_extension_rejected(self):
        with self.assertRaises(ValueError):
            validate_and_process_image(file_bytes=b"whatever", filename="table.gif")

    # -- Case: oversized dimensions (well beyond max, must still resize safely)
    def test_oversized_dimensions_resized_not_rejected(self):
        data = _png_bytes((4000, 5000))
        result = validate_and_process_image(file_bytes=data, filename="huge.png")
        self.assertTrue(result.was_resized)
        self.assertLessEqual(result.width, 2048)
        self.assertLessEqual(result.height, 2048)

    # -- Case: corrupted PNG -> PNG's real verify() (CRC check) still catches it
    def test_corrupted_png_truncated_body_rejected(self):
        good = _png_bytes((200, 200))
        truncated = good[: len(good) - 20]  # drop the tail (IDAT/IEND corrupted)
        with self.assertRaises(ValueError):
            validate_and_process_image(file_bytes=truncated, filename="table.png")


if __name__ == "__main__":
    unittest.main()
