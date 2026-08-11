"""Video preview conversion via PyAV (AV1/AVIF output, HDR preserved)."""

import gc
import io
import os

# Silence the SVT-AV1 encoder's stderr spam, set log level ERROR
os.environ.setdefault("SVT_LOG", "1")

from time import perf_counter

from mediapreview.protocol import PreviewResponse

try:
    import av
    import numpy as np
except ImportError:  # pragma: no cover - optional video extra
    av = None
    np = None


def _rotate_frame_yuv(frame, k):
    """Rotate a planar YUV420 frame by k*90° counter-clockwise, keeping its format.

    Rotating each plane independently preserves the pixel format (including
    10-bit HDR formats like yuv420p10le, which PyAV exposes as uint16 planes)
    and the source colorspace.
    """
    if av is None or np is None:
        raise ImportError(
            "Video previews require the 'video' extra: pip install mediapreview[video]"
        )
    fmt = frame.format
    w, h = frame.width, frame.height
    if (
        not fmt.is_planar
        or fmt.chroma_width(w) * 2 != w
        or fmt.chroma_height(h) * 2 != h
    ):
        raise ValueError(f"unsupported format for YUV rotation: {fmt.name}")
    planes = frame.to_ndarray()
    y, u, v = (
        planes[:h],
        planes[h : h + h // 4].reshape(h // 2, w // 2),
        planes[h + h // 4 :].reshape(h // 2, w // 2),
    )
    planes = np.hstack(
        [p.flat for p in (np.rot90(y, k), np.rot90(u, k), np.rot90(v, k))]
    )
    new_width = w if k % 2 == 0 else h
    return av.VideoFrame.from_ndarray(planes.reshape(-1, new_width), format=fmt.name)


def process_video(path, *, maxsize, quality):
    if av is None or np is None:
        raise ImportError(
            "Video previews require the 'video' extra: pip install mediapreview[video]"
        )
    frame = None
    imgdata = io.BytesIO()
    istream = ostream = icc = occ = frame = None
    t_load_start = perf_counter()
    # Initialize to avoid "possibly unbound" in static analysis when exceptions occur
    t_load_end = t_load_start
    t_save_start = t_load_start
    t_save_end = t_load_start
    with (
        av.open(
            str(path),
            options={
                "analyzeduration": "1000000",  # 1 second (in microseconds)
                "fflags": "fastseek",
            },
        ) as icontainer,
        av.open(imgdata, "w", format="avif") as ocontainer,
    ):
        istream = icontainer.streams.video[0]
        istream.codec_context.skip_frame = "NONKEY"
        icontainer.seek((icontainer.duration or 0) // 8)
        for frame in icontainer.decode(istream):
            if frame.dts is not None:
                break
        else:
            raise RuntimeError("No frames found in video")

        # Resize frame to thumbnail size
        # Capture display dimensions before resize (accounting for rotation)
        disp_w = frame.width
        disp_h = frame.height
        if frame.rotation in (90, 270):
            disp_w, disp_h = disp_h, disp_w
        if frame.width > maxsize or frame.height > maxsize:
            scale_factor = min(maxsize / frame.width, maxsize / frame.height)
            new_width = int(frame.width * scale_factor)
            new_height = int(frame.height * scale_factor)
            frame = frame.reformat(width=new_width, height=new_height)

        # Apply display-matrix rotation if present
        if frame.rotation:
            # frame.rotation indicates clockwise rotation needed to display correctly
            k = (frame.rotation // 90) % 4  # Convert to counter-clockwise rotations
            frame = _rotate_frame_yuv(frame, k)

        # libsvtav1 rejects full-range JPEG-style YUV pixel formats such as
        # yuvj420p, so normalize them before opening the encoder.
        if frame.format.name.startswith("yuvj"):
            frame = frame.reformat(format="yuv420p")
        t_load_end = perf_counter()

        t_save_start = perf_counter()
        crf = str(int(63 * (1 - quality / 100) ** 2))  # Closely matching PIL quality-%
        ostream = ocontainer.add_stream(
            "av1",
            options={
                "crf": crf,
                "usage": "realtime",
                "cpu-used": "8",
                "threads": "1",
            },
        )
        if not isinstance(ostream, av.VideoStream):
            raise TypeError("failed to initialize AV1 video stream")
        ostream.width = frame.width
        ostream.height = frame.height
        ostream.pix_fmt = frame.format.name
        icc = istream.codec_context
        occ = ostream.codec_context

        # Copy HDR metadata from input video stream
        occ.color_primaries = icc.color_primaries
        occ.color_trc = icc.color_trc
        occ.colorspace = icc.colorspace
        occ.color_range = icc.color_range

        ocontainer.mux(ostream.encode(frame))
        ocontainer.mux(ostream.encode(None))  # Flush the stream
        t_save_end = perf_counter()

    # Capture result before cleanup
    ret = imgdata.getvalue()
    resp = PreviewResponse(
        ok=True,
        mime="image/avif",
        backend="video",
        timings=[
            round((t_load_end - t_load_start) * 1000, 1),
            round((t_save_end - t_save_start) * 1000, 1),
        ],
        width=disp_w,
        height=disp_h,
    )
    del imgdata, istream, ostream, icc, occ, frame
    gc.collect()
    return ret, resp
