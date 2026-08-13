"""Media preview framework — low-level converters and a worker pool.

Optional functionality is gated by extras:

    pip install mediapreview[standard]   # all preview backends + worker pool
    pip install mediapreview[worker]     # persistent subprocess worker pool
    pip install mediapreview[pdf]        # PDF previews
    pip install mediapreview[video]      # video previews
    pip install mediapreview[office]     # OnlyOffice document conversion
"""

from mediapreview.backends import (
    dispatch,
    process_image,
    process_image_buffer,
    process_pdf,
    process_video,
)
from mediapreview.cache import CachedPreview, PreviewCache
from mediapreview.exceptions import PreviewError
from mediapreview.formats import is_previewable_path
from mediapreview.protocol import PreviewRequest, PreviewResponse

__all__ = [
    "CachedPreview",
    "PreviewCache",
    "PreviewError",
    "PreviewRequest",
    "PreviewResponse",
    "dispatch",
    "is_previewable_path",
    "process_image",
    "process_image_buffer",
    "process_pdf",
    "process_video",
]
