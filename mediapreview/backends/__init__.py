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
from mediapreview.exceptions import PreviewError, backend_error
from mediapreview.formats import DOC_PREVIEW_SUFFIXES

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
    except PreviewError:
        # Already structured (e.g. a stage of a combined pipeline like
        # pdf+pyvips) — keep the original backend/stage identity.
        raise
    except ValueError as e:
        raise backend_error(backend, str(e)) from e
    except ImportError as e:
        # Missing optional extra — expected, so a plain message, no traceback.
        logger.error("Preview dispatch failed for %s: %s", path, e)  # noqa: TRY400
        raise backend_error(backend, str(e)) from e
    except Exception as e:
        logger.exception("Preview dispatch failed for %s", path)
        raise backend_error(backend, str(e)) from e
    raise backend_error(backend, "preview unsupported")
