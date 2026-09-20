"""Image preview conversion via pyvips (and ffmpeg for HEIC/HEIF/AVIF)."""

import shlex
import subprocess
import tempfile
from pathlib import Path
from time import perf_counter

import pyvips

from mediapreview.protocol import PreviewResponse
from mediapreview.util.logformat import quiet_vips_logging

quiet_vips_logging()

AVIF_FAST_EFFORT = 0


def process_image(path, *, maxsize, quality):
    return process_image_pyvips(path, maxsize=maxsize, quality=quality)


def _get_image_dimensions(path: Path) -> tuple[int, int] | None:
    """Probe image dimensions.

    pyvips can read the header of most formats (including HEIC) without
    fully decoding the image.
    """
    try:
        img = pyvips.Image.new_from_file(str(path))
        img = img.autorot()
    except pyvips.error.Error:
        return None
    else:
        return img.width, img.height


def _image_via_ffmpeg(path: Path, maxsize: int, quality: int) -> bytes:
    """Convert any image to AVIF using ffmpeg CLI.

    ffmpeg handles HEIC tile assembly, HDR metadata and ICC profile embedding
    automatically. Note: -vf cannot be used here — HEIC tile assembly feeds
    the stream from a complex filtergraph, which conflicts with simple -vf
    filtering; scaling must use the -s output option instead.
    """
    dims = _get_image_dimensions(path)
    crf = int(63 * (1 - quality / 100) ** 2)
    with tempfile.NamedTemporaryFile(suffix=".avif", delete=False) as tmp_f:
        tmp_path = tmp_f.name
    cmd = [
        "ffmpeg",
        # Keep error messages, drop the banner/config/stream-mapping spam.
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        # No interactive keyboard prompts ("Press [q] to stop ...").
        "-nostdin",
        "-y",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-c:v",
        "av1",
        "-crf",
        str(crf),
        "-cpu-used",
        "8",
        tmp_path,
    ]
    if dims is not None:
        w, h = dims
        if max(w, h) > maxsize:
            scale = min(maxsize / w, maxsize / h)
            new_w = int(w * scale)
            new_h = int(h * scale)
            # insert -s <wxh> right after the input file
            input_index = cmd.index(str(path)) + 1
            cmd.insert(input_index, "-s")
            cmd.insert(input_index + 1, f"{new_w}x{new_h}")
    try:
        try:
            # stdin=DEVNULL is critical: ffmpeg must not inherit the worker's
            # stdin, which carries the framed request protocol. An inherited
            # stdin lets ffmpeg eat protocol bytes and, if the worker is
            # killed mid-conversion, keeps the orphaned ffmpeg holding the
            # pipe open so the parent's proc.wait() hangs forever.
            subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                check=True,
                shell=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            shell_cmd = shlex.join(cmd)
            stderr = (e.stderr or b"").decode(errors="replace").strip()
            if stderr:
                raise RuntimeError(
                    f"ffmpeg failed (exit {e.returncode}):\n{shell_cmd}\n{stderr}"
                ) from e
            raise RuntimeError(
                f"ffmpeg failed (exit {e.returncode}):\n{shell_cmd}"
            ) from e
        with Path(tmp_path).open("rb") as f:
            return f.read()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def process_image_pyvips(path, *, maxsize, quality):
    t_start = perf_counter()
    suffix = path.suffix.lower()

    # HEIC/HEIF/AVIF: ffmpeg handles tile assembly and HDR correctly;
    # skip pyvips entirely (pyvips drops CICP colour metadata, turning
    # HDR sources into washed-out SDR previews).
    if suffix in (".heic", ".heif", ".avif"):
        dims = _get_image_dimensions(path)
        width, height = dims or (None, None)
        ret = _image_via_ffmpeg(path, maxsize, quality)
        t_end = perf_counter()
        return ret, PreviewResponse(
            ok=True,
            mime="image/avif",
            backend="ffmpeg",
            timings=[round((t_end - t_start) * 1000, 1)],
            width=width,
            height=height,
        )

    # Other image formats: pyvips only. ffmpeg is not a useful fallback
    # here — when pyvips cannot decode a file, ffmpeg's image decoders
    # cannot either, and their failure output is far noisier.
    try:
        img = pyvips.Image.new_from_file(str(path), access="sequential")
        if img.get_typeof("orientation") and img.get("orientation") != 1:
            # autorot's rot90 reads pixels out of order, which sequential
            # access cannot do — reopen with random access when rotating.
            img = pyvips.Image.new_from_file(str(path)).autorot()
        orig_w, orig_h = img.width, img.height
        scale = min(maxsize / img.width, maxsize / img.height, 1.0)
        if scale < 1.0:
            img = img.resize(scale)
        ret = img.write_to_buffer(
            ".avif",
            Q=quality,
            effort=AVIF_FAST_EFFORT,
            keep="none",
        )
    except pyvips.error.Error as e:
        raise ValueError(f"cannot decode image: {e}") from e
    backend = "vips"
    t_end = perf_counter()

    return ret, PreviewResponse(
        ok=True,
        mime="image/avif",
        backend=backend,
        timings=[round((t_end - t_start) * 1000, 1)],
        width=orig_w,
        height=orig_h,
    )


def process_image_buffer(data: bytes, *, quality, maxsize, maxzoom):
    _ = maxzoom
    t_start = perf_counter()
    img = pyvips.Image.new_from_buffer(data, "")
    img = img.autorot()
    orig_w, orig_h = img.width, img.height
    scale = min(maxsize / img.width, maxsize / img.height, 1.0)
    if scale < 1.0:
        img = img.resize(scale)
    ret = img.write_to_buffer(
        ".avif",
        Q=quality,
        effort=AVIF_FAST_EFFORT,
        keep="none",
    )
    t_end = perf_counter()

    return ret, PreviewResponse(
        ok=True,
        mime="image/avif",
        backend="vips",
        timings=[round((t_end - t_start) * 1000, 1)],
        width=orig_w,
        height=orig_h,
    )
