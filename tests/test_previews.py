"""Success tests for low-level preview conversion functions.

These tests exercise the concrete backend converters (image, video, PDF,
office) against the fixture files in tests/files/.  The only thing they
assert is that the conversion succeeds and returns non-empty AVIF bytes, plus
basic metadata sanity checks.

Office tests are skipped unless an OnlyOffice Document Server is reachable.
Configure them with environment variables before running pytest:

    ONLYOFFICE_URL=http://localhost:8988
    ONLYOFFICE_JWT_SECRET=<same-secret-you-gave-the-oo-container>
    ONLYOFFICE_CALLBACK_HOST=<host the OO container can reach>
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mediapreview import dispatch
from mediapreview.backends.image import (
    process_image,
    process_image_buffer,
    process_image_pyvips,
)
from mediapreview.backends.pdf import process_pdf
from mediapreview.backends.video import process_video
from mediapreview.exceptions import PreviewBackendError
from mediapreview.office import is_available_async
from mediapreview.pool import generate_office_preview

FILES = Path(__file__).resolve().parent / "files"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_ok(data, resp, backend: str | None = None) -> None:
    assert resp.ok, f"conversion failed: {resp.error}"
    assert data
    assert resp.mime == "image/avif"
    if backend:
        assert resp.backend == backend


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(FILES.glob("Landscape_*.jpg")))
def test_process_image_exif_orientations(path: Path) -> None:
    """Every EXIF orientation fixture must produce a valid preview."""
    data, resp = process_image(path, maxsize=512, quality=60)
    _assert_ok(data, resp, backend="vips")
    assert resp.width in (1200, 1800)
    assert resp.height in (1200, 1800)


def test_process_image_hdr_avif() -> None:
    """HDR AVIF images are routed through ffmpeg to preserve colour metadata."""
    path = FILES / "hdr_cosmos01650_cicp9-16-9_yuv444_full_qp10.avif"
    data, resp = process_image(path, maxsize=512, quality=60)
    _assert_ok(data, resp, backend="ffmpeg")
    assert resp.width is not None
    assert resp.height is not None


def test_process_image_pyvips() -> None:
    """The pyvips-only image backend works on a plain JPEG."""
    path = FILES / "Landscape_1.jpg"
    data, resp = process_image_pyvips(path, maxsize=512, quality=60)
    _assert_ok(data, resp, backend="vips")


def test_process_image_buffer() -> None:
    """Processing a JPEG from a buffer produces the same valid preview."""
    path = FILES / "Landscape_1.jpg"
    data, resp = process_image_buffer(
        path.read_bytes(), maxsize=512, quality=60, maxzoom=2.0
    )
    _assert_ok(data, resp, backend="vips")


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------


VIDEO_FIXTURES = [
    ("sample-1mb.mp4", 854, 480),
    ("rotated_90.mp4", 480, 854),
    ("rotated_270.mp4", 480, 854),
    ("hdr_video.mp4", 320, 240),
    ("hdr_rotated_90.mp4", 240, 320),
    ("hdr_rotated_270.mp4", 240, 320),
]


@pytest.mark.parametrize(
    ("filename", "expected_width", "expected_height"),
    VIDEO_FIXTURES,
    ids=[f[0] for f in VIDEO_FIXTURES],
)
def test_process_video(
    filename: str, expected_width: int, expected_height: int
) -> None:
    """SDR and HDR video clips, with and without rotation, convert successfully."""
    data, resp = process_video(FILES / filename, maxsize=512, quality=60)
    _assert_ok(data, resp, backend="video")
    assert resp.width == expected_width
    assert resp.height == expected_height


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_process_pdf() -> None:
    """A simple single-page PDF converts to a valid preview."""
    data, resp = process_pdf(
        FILES / "sample.pdf",
        maxsize=512,
        maxzoom=2.0,
        quality=60,
    )
    _assert_ok(data, resp, backend="pdf+vips")
    assert resp.width == 595
    assert resp.height == 842


# ---------------------------------------------------------------------------
# Public dispatch entry point
# ---------------------------------------------------------------------------


DISPATCH_FIXTURES = [
    ("Landscape_1.jpg", "vips", 1800, 1200),
    ("sample-1mb.mp4", "video", 854, 480),
    ("sample.pdf", "pdf+vips", 595, 842),
]


@pytest.mark.parametrize(
    ("filename", "backend", "expected_width", "expected_height"),
    DISPATCH_FIXTURES,
    ids=[f[0] for f in DISPATCH_FIXTURES],
)
def test_dispatch(
    filename: str, backend: str, expected_width: int, expected_height: int
) -> None:
    """The public dispatch() wrapper routes to the correct backend and succeeds."""
    data, resp = dispatch(FILES / filename, quality=60, maxsize=512, maxzoom=2.0)
    _assert_ok(data, resp, backend=backend)
    assert resp.width == expected_width
    assert resp.height == expected_height


def test_dispatch_office(monkeypatch) -> None:
    """dispatch() converts office documents via OnlyOffice when called directly."""
    fake_png = (FILES / "Landscape_1.jpg").read_bytes()

    class _FakeManager:
        async def convert(self, filepath: Path) -> bytes:
            assert filepath == FILES / "file-sample_100kB.docx"
            return fake_png

    async def _noop() -> None:
        return None

    monkeypatch.setattr("mediapreview.office.get_oo_manager", _FakeManager)
    monkeypatch.setattr("mediapreview.office.close_oo_client", _noop)

    data, resp = dispatch(
        FILES / "file-sample_100kB.docx",
        quality=60,
        maxsize=512,
        maxzoom=2.0,
    )
    _assert_ok(data, resp)
    assert resp.backend == "onlyoffice+vips"


def test_dispatch_unknown_extension(tmp_path: Path) -> None:
    """Unsupported extensions produce a diagnostic naming the extension."""
    path = tmp_path / "unknown-file.xyz"
    path.write_text("not a previewable file")
    with pytest.raises(PreviewBackendError) as exc_info:
        dispatch(path, quality=60, maxsize=512, maxzoom=2.0)
    assert "unknown file extension: '.xyz'" in str(exc_info.value)
    assert exc_info.value.backend == "unknown"


def test_dispatch_no_extension(tmp_path: Path) -> None:
    """Files without an extension produce a diagnostic saying so."""
    path = tmp_path / "unknown-file-no-ext"
    path.write_text("not a previewable file")
    with pytest.raises(PreviewBackendError) as exc_info:
        dispatch(path, quality=60, maxsize=512, maxzoom=2.0)
    assert "unknown file type: no file extension" in str(exc_info.value)
    assert exc_info.value.backend == "unknown"


# ---------------------------------------------------------------------------
# Office previews via OnlyOffice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_office_preview() -> None:
    """DOCX preview through OnlyOffice.

    Skipped unless an OnlyOffice server is reachable and ONLYOFFICE_JWT_SECRET
    is set to the same secret the OO container is using. The shared secret must
    be at least 32 bytes long so PyJWT doesn't warn about weak HMAC keys.
    """
    secret = os.environ.get("ONLYOFFICE_JWT_SECRET", "")
    if len(secret.encode()) < 32:
        pytest.skip("ONLYOFFICE_JWT_SECRET not set or shorter than 32 bytes")
    if not await is_available_async():
        pytest.skip("OnlyOffice Document Server not reachable")

    data, resp = await generate_office_preview(
        FILES / "file-sample_100kB.docx",
        quality=60,
        maxsize=512,
        maxzoom=2.0,
    )
    assert data is not None
    assert resp is not None
    _assert_ok(data, resp)
    assert resp.width is not None
    assert resp.height is not None
