#!/usr/bin/env python3
"""Generate missing test fixtures for the low-level preview tests.

This script only creates files that are not already present in tests/files/.
Run it whenever you need to rebuild the rotated/HDR video fixtures or the
sample PDF.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError as e:  # pragma: no cover - optional pdf extra
    raise ImportError(
        "Fixture generation requires pymupdf: pip install mediapreview[pdf]"
    ) from e

ROOT = Path(__file__).resolve().parent
FILES = ROOT
logger = logging.getLogger(__name__)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _find_box(data: bytearray, box_type: bytes, start: int = 0, end: int | None = None) -> int:
    end = end or len(data)
    i = start
    while i + 8 <= end:
        size = int.from_bytes(data[i : i + 4], "big")
        btype = data[i + 4 : i + 8]
        if btype == box_type:
            return i
        if size == 0:
            break
        if size == 1:
            size = int.from_bytes(data[i + 8 : i + 16], "big")
        if size < 8:
            raise ValueError(f"Invalid box size {size} for {btype.decode('ascii', errors='replace')}")
        i += size
    return -1


def _matrix_90_cw() -> list[int]:
    return [0, 1 << 16, 0, -(1 << 16), 0, 0, 0, 0, 1 << 16]


def _matrix_270_cw() -> list[int]:
    return [0, -(1 << 16), 0, 1 << 16, 0, 0, 0, 0, 1 << 16]


def _patch_tkhd_rotation(in_path: Path, out_path: Path, degrees: int) -> None:
    """Write a copy of *in_path* with the video track display matrix rotated."""
    data = bytearray(in_path.read_bytes())

    moov_idx = _find_box(data, b"moov")
    if moov_idx < 0:
        raise ValueError("moov box not found")
    moov_end = moov_idx + int.from_bytes(data[moov_idx : moov_idx + 4], "big")

    trak_idx = _find_box(data, b"trak", moov_idx + 8, moov_end)
    while trak_idx >= 0:
        trak_size = int.from_bytes(data[trak_idx : trak_idx + 4], "big")
        trak_end = trak_idx + trak_size
        mdia_idx = _find_box(data, b"mdia", trak_idx + 8, trak_end)
        if mdia_idx < 0:
            trak_idx = _find_box(data, b"trak", trak_end, moov_end)
            continue
        mdia_end = mdia_idx + int.from_bytes(data[mdia_idx : mdia_idx + 4], "big")
        hdlr_idx = _find_box(data, b"hdlr", mdia_idx + 8, mdia_end)
        if hdlr_idx < 0:
            trak_idx = _find_box(data, b"trak", trak_end, moov_end)
            continue
        handler_type = data[hdlr_idx + 16 : hdlr_idx + 20]
        if handler_type == b"vide":
            tkhd_idx = _find_box(data, b"tkhd", trak_idx + 8, trak_end)
            if tkhd_idx < 0:
                raise ValueError("video track has no tkhd")
            version = data[tkhd_idx + 8]
            if version != 0:
                raise ValueError(f"unsupported tkhd version {version}")
            matrix_offset = tkhd_idx + 48
            matrix = _matrix_90_cw() if degrees == 90 else _matrix_270_cw()
            for i, val in enumerate(matrix):
                data[matrix_offset + i * 4 : matrix_offset + (i + 1) * 4] = val.to_bytes(
                    4, "big", signed=True
                )
            out_path.write_bytes(data)
            return
        trak_idx = _find_box(data, b"trak", trak_end, moov_end)
    raise ValueError("video track not found")


def generate_pdf() -> None:
    pdf_path = FILES / "sample.pdf"
    if pdf_path.exists():
        return
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 100), "Hello, PDF preview test!")
    page.draw_rect((100, 150, 400, 250), color=(1, 0, 0), width=2)
    doc.save(str(pdf_path))
    doc.close()
    logger.info("generated %s", pdf_path)


def generate_sdr_rotated() -> None:
    source = FILES / "sample-1mb.mp4"
    if not source.exists():
        raise FileNotFoundError(f"Missing source fixture: {source}")
    for degrees in (90, 270):
        out = FILES / f"rotated_{degrees}.mp4"
        if out.exists():
            continue
        _patch_tkhd_rotation(source, out, degrees)
        logger.info("generated %s", out)


def generate_hdr_video() -> None:
    out = FILES / "hdr_video.mp4"
    if out.exists():
        return
    run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-c:v",
            "libx265",
            "-preset",
            "fast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p10le",
            "-x265-params",
            "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:repeat-headers=1",
            "-an",
            str(out),
        ]
    )
    logger.info("generated %s", out)


def generate_hdr_rotated() -> None:
    source = FILES / "hdr_video.mp4"
    if not source.exists():
        raise FileNotFoundError(f"Generate hdr_video.mp4 first: {source}")
    for degrees in (90, 270):
        out = FILES / f"hdr_rotated_{degrees}.mp4"
        if out.exists():
            continue
        _patch_tkhd_rotation(source, out, degrees)
        logger.info("generated %s", out)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate_pdf()
    generate_sdr_rotated()
    generate_hdr_video()
    generate_hdr_rotated()
    return 0


if __name__ == "__main__":
    sys.exit(main())
