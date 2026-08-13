"""File-type classification for preview generation.

Suffix sets and helpers that decide which backend handles a given path,
how previewable it is, and how jobs should be prioritised.
"""

import mimetypes
from pathlib import Path

DOC_PREVIEW_SUFFIXES = {".pdf", ".xps", ".epub", ".mobi"}

OFFICE_PREVIEW_SUFFIXES = {
    ".doc",
    ".dot",
    ".docx",
    ".docm",
    ".dotx",
    ".dotm",
    ".rtf",
    ".odt",
    ".ott",
    ".txt",
    ".md",
    ".mhtml",
    ".mht",
    ".html",
    ".htm",
    ".xml",
    ".wps",
    ".wri",
    # Spreadsheets
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlsb",
    ".xltx",
    ".xltm",
    ".ods",
    ".ots",
    ".csv",
    # Presentations
    ".ppt",
    ".pptx",
    ".pptm",
    ".pps",
    ".ppsx",
    ".pot",
    ".potx",
    ".odp",
    ".otp",
}


def is_previewable_path(path) -> bool:
    suffix = path.suffix.lower()
    if suffix in DOC_PREVIEW_SUFFIXES or suffix in OFFICE_PREVIEW_SUFFIXES:
        return True
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        return False
    return mime_type.startswith(("image/", "video/"))


def preview_job_priority(path) -> int:
    """Return priority for preview job (lower=higher priority).

    Priority order: images (0) < video (1) < PDF (2) < office (3) < unknown (4)
    """
    suffix = path.suffix.lower()
    if suffix in DOC_PREVIEW_SUFFIXES:
        return 2
    if suffix in OFFICE_PREVIEW_SUFFIXES:
        return 3
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type and mime_type.startswith("image/"):
        return 0
    if mime_type and mime_type.startswith("video/"):
        return 1
    return 4


def expected_backend(path: Path) -> str:
    """Best-effort backend label used for timeout/access logging."""
    suffix = path.suffix.lower()
    if suffix in OFFICE_PREVIEW_SUFFIXES:
        return "onlyoffice"
    if suffix in DOC_PREVIEW_SUFFIXES:
        return "pdf"
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type and mime_type.startswith("video/"):
        return "video"
    if mime_type and mime_type.startswith("image/"):
        return "vips"
    return "preview"
