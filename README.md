# mediapreview

Generate compact AVIF preview images from images, videos, PDFs and office documents.

`mediapreview` is a small library of low-level converters. Give it a file path and
it returns AVIF bytes plus a response object telling you which backend handled it
and whether it succeeded.

## Install

```sh
uv add mediapreview[standard]
```

`[standard]` pulls in every backend and the optional worker pool. Use the feature-specific extras to avoid dependencies not needed for your application.

## Quick start

```python
from pathlib import Path
from mediapreview import dispatch

avif_bytes, resp = dispatch(
    Path("photo.jpg"),
    quality=60,
    maxsize=512,
    maxzoom=2.0,
)

if resp.ok:
    Path("preview.avif").write_bytes(avif_bytes)
else:
    print(resp.error)
```

`dispatch` picks the backend from the file extension or mimetype. You can also
call the backend functions directly:

```python
from mediapreview import process_image, process_pdf, process_video

avif, resp = process_image(Path("photo.jpg"), maxsize=512, quality=60)
avif, resp = process_pdf(Path("doc.pdf"), maxsize=512, quality=60, page_number=0)
avif, resp = process_video(Path("clip.mp4"), maxsize=512, quality=60)
```

## Worker pool (optional)

Heavy native dependencies and crashes stay out of your async loop by running
previews in a pool of persistent subprocess workers.

```python
from mediapreview.pool import (
    start_preview_workers,
    shutdown_preview_workers,
    run_preview,
)

await start_preview_workers()
try:
    avif, resp = await run_preview(Path("doc.pdf"))
finally:
    await shutdown_preview_workers()
```

Add the `worker` extra to use the pool.

## CLI

```bash
mediapreview photo.jpg -o preview.avif
mediapreview doc.pdf -q 70 --maxsize 1024
mediapreview oosetup    # build + run the bundled OnlyOffice container
```

## OnlyOffice setup

Office previews need an OnlyOffice Document Server. A patched Docker image ships
inside the package and can be started with:

```bash
mediapreview oosetup [name] [port]
```

`oosetup` logs to stderr and prints one line on stdout:

```
ONLYOFFICE_JWT_SECRET=<token>
```

Set `ONLYOFFICE_JWT_SECRET` yourself to reuse an existing secret; otherwise a
random one is generated.
