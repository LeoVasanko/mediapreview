"""Low-level preview conversion backends.

Each backend module exposes a `process_*` function taking a path (or buffer)
plus quality/size parameters and returning `(avif_bytes, PreviewResponse)`.
`dispatch` picks the right backend for a path.
"""

import logging
import mimetypes

from mediapreview.backends.image import (
    process_image,
    process_image_buffer,
    process_image_pyvips,
)
from mediapreview.backends.pdf import process_pdf
from mediapreview.backends.video import process_video
from mediapreview.formats import DOC_PREVIEW_SUFFIXES
from mediapreview.protocol import PreviewResponse

__all__ = [
    "dispatch",
    "process_image",
    "process_image_buffer",
    "process_image_pyvips",
    "process_pdf",
    "process_video",
]

logger = logging.getLogger(__name__)


def dispatch(path, quality, maxsize, maxzoom, data=None):
    backend = "unknown"
    try:
        if data:
            backend = "pyvips"
            return process_image_buffer(
                data, quality=quality, maxsize=maxsize, maxzoom=maxzoom
            )
        suffix = path.suffix.lower()
        if suffix in DOC_PREVIEW_SUFFIXES:
            backend = "pdf"
            return process_pdf(path, quality=quality, maxsize=maxsize, maxzoom=maxzoom)
        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type and mime_type.startswith("video/"):
            backend = "video"
            return process_video(path, quality=quality, maxsize=maxsize)
        if mime_type and mime_type.startswith("image/"):
            backend = "pyvips"
            return process_image(path, quality=quality, maxsize=maxsize)
    except ValueError as e:
        return None, PreviewResponse(ok=False, backend=backend, error=str(e))
    except Exception as e:
        logger.exception("Preview dispatch failed for %s", path)
        return None, PreviewResponse(ok=False, backend=backend, error=str(e))
    return None, PreviewResponse(ok=False, backend=backend, error="preview unsupported")
