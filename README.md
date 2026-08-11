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

One-shot CLI (works with the base install, no worker extra needed):

```bash
mediapreview photo.jpg -o preview.avif        # or: python -m mediapreview ...
mediapreview doc.pdf -q 70 --maxsize 1024     # needs the matching backend extra
```

## OnlyOffice Docker bootstrap

A patched OnlyOffice image (configurable converter worker count) ships as
package data and can be built/started with:

```bash
mediapreview oosetup    # builds + runs "onlyoffice-mediapreview" on port 8988
```

Container name and port are optional positional args:
`mediapreview oosetup [name] [port]`.

`oosetup` logs progress to stderr and prints exactly one line on stdout:

```
ONLYOFFICE_JWT_SECRET=<token>
```

If `ONLYOFFICE_JWT_SECRET` is already set in the environment it is used as-is
(and echoed back); otherwise a random secret is generated. Persist the token
wherever your deployment keeps its configuration and export it for later runs
— the caller owns the secret, mediapreview does not store it.
