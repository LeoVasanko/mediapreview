"""PDF/XPS/EPUB preview conversion via PyMuPDF + pyvips."""

from time import perf_counter

import pyvips

from mediapreview.backends.image import AVIF_FAST_EFFORT
from mediapreview.protocol import PreviewResponse

try:
    import pymupdf
except ImportError:  # pragma: no cover - optional pdf extra
    pymupdf = None


def process_pdf(path, *, maxsize, maxzoom, quality, page_number=0):
    if pymupdf is None:
        raise ImportError(
            "PDF previews require the 'pdf' extra: pip install mediapreview[pdf]"
        )
    t_load_start = perf_counter()
    with pymupdf.open(path) as pdf:
        page = pdf.load_page(page_number)
        w, h = page.rect[2:4]
        zoom = min(maxsize / w, maxsize / h, maxzoom)
        mat = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        t_load_end = perf_counter()

        t_save_start = perf_counter()
        img = pyvips.Image.new_from_memory(
            pix.samples_mv, pix.width, pix.height, pix.n, "uchar"
        )
    ret = img.write_to_buffer(".avif", Q=quality, effort=AVIF_FAST_EFFORT, keep="none")
    backend = "pdf+pyvips"
    t_save_end = perf_counter()

    return ret, PreviewResponse(
        ok=True,
        mime="image/avif",
        backend=backend,
        timings=[
            round((t_load_end - t_load_start) * 1000, 1),
            round((t_save_end - t_save_start) * 1000, 1),
        ],
        width=round(w),
        height=round(h),
    )
