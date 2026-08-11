# mediapreview

Low-level media preview converters plus an optional persistent worker pool
framework. All converters produce AVIF output.

## Layout

| Module | Purpose |
|--------|---------|
| `mediapreview.backends.image` | Image → AVIF via pyvips (ffmpeg for HEIC/HEIF/AVIF) |
| `mediapreview.backends.video` | Video frame → AVIF via PyAV (HDR preserved) |
| `mediapreview.backends.pdf` | PDF/XPS/EPUB page → AVIF via PyMuPDF + pyvips |
| `mediapreview.backends` | `dispatch()` — pick a backend by path/mimetype |
| `mediapreview.formats` | Suffix sets, previewability and priority classification |
| `mediapreview.office` | OnlyOffice Document Server client + Docker bootstrap |
| `mediapreview.docker/` | Patched OnlyOffice image build context (ships in the wheel) |
| `mediapreview.pool` | Async persistent subprocess worker pool (optional) |
| `mediapreview.worker` | Worker subprocess entry point (framed stdin/stdout protocol) |
| `mediapreview.protocol` | msgspec wire structs (`PreviewRequest` / `PreviewResponse`) |
| `mediapreview.cache` | Thread-safe LRU cache for preview responses |

## Extras

```bash
pip install mediapreview            # image converter only (pyvips)
pip install mediapreview[pdf]       # + PDF/XPS/EPUB (pymupdf)
pip install mediapreview[video]     # + video (av, numpy)
pip install mediapreview[office]    # + OnlyOffice client (httpx, pyjwt)
pip install mediapreview[worker]    # + worker pool (blake3, tracerite)
pip install mediapreview[standard]  # everything
```

## Usage

Low-level, in-process:

```python
from mediapreview import dispatch

avif_bytes, resp = dispatch(path, quality=60, maxsize=512, maxzoom=2.0)
```

Worker pool (isolates heavy imports and native crashes from the async loop):

```python
from mediapreview.pool import start_preview_workers, shutdown_preview_workers
```

## OnlyOffice Docker bootstrap

A patched OnlyOffice image (configurable converter worker count) ships as
package data and can be built/started with:

```python
from mediapreview.office import setup_docker

setup_docker()  # builds + runs "onlyoffice-mediapreview" on port 8988
```
