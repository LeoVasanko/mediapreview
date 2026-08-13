"""Structured preview exceptions.

Preview failures are represented by ``PreviewError`` and a small number of
subclasses, carrying their fields directly: ``short`` (concise, for UI with
limited space — the backend name is usually printed in front of it, so it is
left out), the exception message itself (for logs), ``backend``, and
optional subclass-specific metadata such as ``code`` or ``timeout_seconds``
for callers that wish to do their own processing.

The worker pool ships exceptions between processes with pickle (same
trust domain — the pool unpickles only data from its own workers), so any
exception arrives intact on the caller side, no per-class serialization
machinery needed. Because the initializers are keyword-heavy, pickling is
routed through ``__dict__`` via ``PreviewError.__reduce__``.

The hierarchy is intentionally small:

- ``OnlyOfficeError`` covers all OnlyOffice failures; optional fields
  (``code``, ``status``, ``url``, ``snippet``) describe the specific failure.
- ``PreviewBackendError`` covers backend conversion failures (ffmpeg, pyvips,
  pdf, etc.); ``stage`` identifies the failing step of a combined pipeline
  (e.g. "pdf" vs "pyvips" in the "pdf+pyvips" backend).
- ``PreviewTimeoutError`` covers timeouts for any backend.
- ``PreviewCancelledError`` covers cancellations (e.g. pool shutdown).

BaseExceptions such as ``KeyboardInterrupt``, ``SystemExit`` and
``asyncio.CancelledError`` are never wrapped in these types.
"""

from __future__ import annotations


class PreviewError(Exception):
    """Base preview exception.

    ``short`` is concise text for UIs with limited space (the backend name
    is usually printed in front of it); ``str(err)`` is the full
    human-readable message; ``backend`` identifies the backend
    (e.g. "onlyoffice", "ffmpeg").
    """

    def __init__(
        self,
        message: str = "preview failed",
        short: str = "error",
        *,
        backend: str | None = None,
    ):
        super().__init__(message)
        self.short = short
        self.backend = backend

    def __reduce__(self):
        # Keyword-heavy initializers do not unpickle via args; pass the message
        # positionally and restore the rest from __dict__.
        return (type(self), (str(self),), self.__dict__)


class OnlyOfficeError(PreviewError):
    """OnlyOffice conversion failed. Specifics are in the extra fields."""

    def __init__(  # noqa: PLR0913 - metadata fields are independent
        self,
        message: str = "OnlyOffice conversion failed",
        short: str = "error",
        *,
        code: str | None = None,
        status: int | None = None,
        url: str | None = None,
        snippet: str | None = None,
        backend: str | None = "onlyoffice",
    ):
        super().__init__(message, short, backend=backend)
        self.code = code
        self.status = status
        self.url = url
        self.snippet = snippet


class PreviewBackendError(PreviewError):
    """Backend conversion failure (image/video/pdf/etc)."""

    def __init__(
        self,
        message: str = "preview failed",
        short: str = "error",
        *,
        stage: str | None = None,
        backend: str | None = None,
    ):
        super().__init__(message, short, backend=backend)
        self.stage = stage


class PreviewTimeoutError(PreviewError):
    """Preview conversion exceeded its timeout for a given backend."""

    def __init__(
        self,
        message: str = "preview timed out",
        short: str = "timeout",
        *,
        timeout_seconds: float = 0.0,
        backend: str | None = None,
    ):
        super().__init__(message, short, backend=backend)
        self.timeout_seconds = timeout_seconds


class PreviewCancelledError(PreviewError):
    """Preview was cancelled (e.g. pool shut down)."""

    def __init__(
        self,
        message: str = "Preview cancelled (pool closed)",
        short: str = "cancelled",
        *,
        reason: str = "pool closed",
        backend: str | None = None,
    ):
        super().__init__(message, short, backend=backend)
        self.reason = reason


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

_OO_CODE_ERRORS = {
    "-8": ("jwt error", "OnlyOffice JWT authentication failed"),
    "-4": ("input error", "OnlyOffice input error"),
    "-2": ("timeout error", "OnlyOffice conversion timed out"),
    "-1": ("unknown error", "OnlyOffice conversion failed with unknown error"),
}


def onlyoffice_error_from_code(code: str | None = None) -> OnlyOfficeError:
    """Build an OnlyOfficeError from a conversion status code (e.g. "-8")."""
    if code in _OO_CODE_ERRORS:
        short, log = _OO_CODE_ERRORS[code]
    elif code:
        short, log = f"{code} error", f"OnlyOffice conversion failed: {code}"
    else:
        short, log = "unknown error", "OnlyOffice conversion failed with unknown error"
    return OnlyOfficeError(log, short, code=code)


def onlyoffice_unavailable_error(url: str | None = None) -> OnlyOfficeError:
    log = "OnlyOffice document server not reachable"
    if url:
        log = f"{log} at {url}"
    return OnlyOfficeError(log, "unavailable", url=url)


def onlyoffice_http_error(status: int) -> OnlyOfficeError:
    return OnlyOfficeError(f"OnlyOffice HTTP error: {status}", "http error", status=status)


def onlyoffice_no_fileurl_error(snippet: str | None = None) -> OnlyOfficeError:
    log = "OnlyOffice response did not contain FileUrl"
    if snippet:
        log = f"{log}: {snippet}"
    return OnlyOfficeError(log, "no-fileurl error", snippet=snippet)


def backend_error(backend: str, message: str, *, stage: str | None = None) -> PreviewBackendError:
    short = message.splitlines()[0][:60]
    return PreviewBackendError(
        f"[{backend}] preview failed: {message}",
        short,
        backend=backend,
        stage=stage,
    )


def preview_timeout_error(backend: str, timeout_seconds: float) -> PreviewTimeoutError:
    return PreviewTimeoutError(
        f"{backend.capitalize()} preview timed out after {timeout_seconds}s",
        "timeout",
        backend=backend,
        timeout_seconds=timeout_seconds,
    )


def preview_cancelled_error(reason: str = "pool closed") -> PreviewCancelledError:
    return PreviewCancelledError(f"Preview cancelled ({reason})", "cancelled", reason=reason)
