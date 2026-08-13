"""Tests for OnlyOffice error handling and structured responses."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from mediapreview import office
from mediapreview.exceptions import (
    OnlyOfficeError,
    PreviewBackendError,
    backend_error,
    onlyoffice_error_from_code,
    onlyoffice_http_error,
    onlyoffice_no_fileurl_error,
    onlyoffice_unavailable_error,
)
from mediapreview.pool import generate_office_preview

FILES = Path(__file__).resolve().parent / "files"


@pytest.mark.parametrize(
    ("code", "short", "message"),
    [
        ("-8", "jwt error", "OnlyOffice JWT authentication failed"),
        ("-4", "input error", "OnlyOffice input error"),
        ("-2", "timeout error", "OnlyOffice conversion timed out"),
        ("-1", "unknown error", "OnlyOffice conversion failed with unknown error"),
        ("-99", "-99 error", "OnlyOffice conversion failed: -99"),
        (None, "unknown error", "OnlyOffice conversion failed with unknown error"),
    ],
)
def test_onlyoffice_error_from_code(code, short, message):
    """Conversion status codes map to clean labels and messages."""
    err = onlyoffice_error_from_code(code)
    assert str(err) == message
    assert err.short == short
    assert err.code == code
    assert err.backend == "onlyoffice"
    assert isinstance(err, OnlyOfficeError)


def test_onlyoffice_http_error():
    err = onlyoffice_http_error(502)
    assert str(err) == "OnlyOffice HTTP error: 502"
    assert err.status == 502
    assert err.short == "http error"


def test_onlyoffice_unavailable_error():
    err = onlyoffice_unavailable_error("http://localhost:8988")
    assert "http://localhost:8988" in str(err)
    assert err.url == "http://localhost:8988"
    assert err.short == "unavailable"


def test_onlyoffice_no_fileurl_error():
    err = onlyoffice_no_fileurl_error("<empty />")
    assert "<empty />" in str(err)
    assert err.snippet == "<empty />"
    assert err.short == "no-fileurl error"


def test_backend_error_pipeline_backend():
    """Combined pipelines report the failing step in the backend name."""
    err = backend_error("pdf", "cannot read document")
    assert err.backend == "pdf"
    assert err.short == "cannot read document"
    assert isinstance(err, PreviewBackendError)


def test_backend_error_short_message_strips_source_and_detail():
    """Backend messages like "source: summary: detail" become just the summary."""
    err = backend_error(
        "vips",
        "pyvips: cannot decode image: unable to load from file b'/mnt/c/Users...",
    )
    assert err.short == "cannot decode image"


def test_error_pickle_round_trip():
    """Exceptions survive pickling (the worker pool wire) intact."""
    err = onlyoffice_error_from_code("-8")
    restored = pickle.loads(pickle.dumps(err))
    assert type(restored) is type(err)
    assert str(restored) == str(err)
    assert restored.code == err.code
    assert restored.short == err.short
    assert restored.backend == err.backend


@pytest.mark.asyncio
async def test_generate_office_preview_raises_structured_error(monkeypatch):
    """On OnlyOffice failure, generate_office_preview raises OnlyOfficeError."""
    async def fake_convert(_filepath: Path, request_timeout: float = 5.0) -> bytes:
        raise onlyoffice_error_from_code("-8")

    monkeypatch.setattr(office, "convert_to_png_async", fake_convert)

    with pytest.raises(OnlyOfficeError) as exc_info:
        await generate_office_preview(
            FILES / "file-sample_100kB.docx",
            quality=60,
            maxsize=512,
            maxzoom=2.0,
        )

    err = exc_info.value
    assert err.code == "-8"
    assert err.short == "jwt error"
    assert str(err) == "OnlyOffice JWT authentication failed"
