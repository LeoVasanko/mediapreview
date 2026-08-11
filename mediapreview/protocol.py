"""Wire protocol structs for the preview worker pool."""

import msgspec


class PreviewRequest(msgspec.Struct, omit_defaults=True):
    path: str
    quality: int
    maxsize: int
    maxzoom: float


class PreviewResponse(msgspec.Struct, omit_defaults=True):
    ok: bool
    mime: str | None = None
    backend: str | None = None
    timings: list[float] | None = None
    error: str | None = None
    stderr: str | None = None
    width: int | None = None
    height: int | None = None
