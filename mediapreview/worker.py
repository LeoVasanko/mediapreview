"""Preview generation worker subprocess entry point.

The actual conversion logic lives in `mediapreview.backends`; this module
only implements the worker process shell around it.

Two modes are supported:
1) Legacy one-shot mode: argv has path/quality/maxsize/maxzoom.
2) Long-lived mode: read framed requests from stdin and write framed responses.

Framed request format (stdin):
    (uint32 json size)(uint32 data size)(json)(binary data)

Framed response format (stdout):
    (blake3(packet))(uint32 json size)(uint32 payload size)(json)(binary payload)
where packet = (uint32 json size)(uint32 payload size)(json)(binary payload).
"""

import contextlib
import io
import logging
import os
import signal
import struct
import sys
from pathlib import Path

import msgspec

try:
    import tracerite
    from blake3 import blake3
except ImportError:  # pragma: no cover - optional worker extra
    sys.stderr.write(
        "mediapreview worker requires the 'worker' extra:"
        " pip install mediapreview[worker]\n"
    )
    sys.exit(1)

from mediapreview.backends import dispatch
from mediapreview.protocol import PreviewRequest, PreviewResponse
from mediapreview.util.logformat import format_level_prefix

logger = logging.getLogger(__name__)


class _WorkerLogFormatter(logging.Formatter):
    """Emoji level prefix like the main process, tagged with the worker pid."""

    def format(self, record: logging.LogRecord) -> str:
        prefix = format_level_prefix(record.levelno)
        return f"{prefix}worker[{os.getpid()}]: {record.getMessage()}"


_enc = msgspec.json.Encoder()
_dec_req = msgspec.json.Decoder(PreviewRequest)


def _read_exactly(f, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _read_request() -> tuple[PreviewRequest, bytes] | None:
    try:
        header = _read_exactly(sys.stdin.buffer, 8)
    except EOFError:
        return None
    json_size, data_size = struct.unpack("<II", header)
    meta_raw = _read_exactly(sys.stdin.buffer, json_size)
    data = b""
    if data_size:
        data = _read_exactly(sys.stdin.buffer, data_size)
    req = _dec_req.decode(meta_raw)
    return req, data


# Raw stdout buffer reserved for the binary protocol once main() redirects
# Python-level stdout to stderr. None means "use sys.stdout.buffer as-is"
# (CLI single-shot mode, where real stdout is wanted).
_protocol_out = None


def _write_response(resp: PreviewResponse, payload: bytes) -> None:
    out = _protocol_out if _protocol_out is not None else sys.stdout.buffer
    meta_bytes = _enc.encode(resp)
    packet = struct.pack("<II", len(meta_bytes), len(payload)) + meta_bytes + payload
    checksum = blake3(packet).digest()
    out.write(checksum)
    out.write(packet)
    out.flush()


def _run_once() -> None:
    if len(sys.argv) != 5:
        sys.stderr.write(f"Usage: {sys.argv[0]} <path> <quality> <maxsize> <maxzoom>\n")
        sys.exit(1)

    path = Path(sys.argv[1])
    quality = int(sys.argv[2])
    maxsize = int(sys.argv[3])
    maxzoom = float(sys.argv[4])
    result, _ = dispatch(path, quality, maxsize, maxzoom)
    if result:
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()


def _run_loop() -> None:
    while True:
        result = _read_request()
        if result is None:
            return
        req, data = result
        stderr_capture = io.StringIO()
        handler = logging.StreamHandler(stderr_capture)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with contextlib.redirect_stderr(stderr_capture):
                result, resp = dispatch(
                    Path(req.path), req.quality, req.maxsize, req.maxzoom, data
                )
            if not resp.ok:
                captured = stderr_capture.getvalue().strip()
                if captured:
                    resp = PreviewResponse(
                        ok=False,
                        backend=resp.backend,
                        error=resp.error,
                        stderr=captured,
                    )
            _write_response(resp, result or b"")
        except Exception as e:
            logger.exception("Preview worker error for %s", req.path)
            captured = stderr_capture.getvalue().strip()
            _write_response(
                PreviewResponse(ok=False, error=str(e), stderr=captured or None), b""
            )
        finally:
            root_logger.removeHandler(handler)
            handler.close()


def main() -> None:
    # Format tracebacks like the main process.
    tracerite.load()
    # Configure all log output to stderr before any imports that may emit
    # logs. stderr is inherited by the parent, so this lands in the server
    # log, formatted like the main process and tagged with the worker pid.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_WorkerLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # pyvips is chatty at INFO ("threadpool completed ..." per operation).
    logging.getLogger("pyvips").setLevel(logging.WARNING)
    # NOTE: standalone package no longer depends on cista config loading.
    # Consumers can load their own configuration before starting workers.
    if len(sys.argv) > 1:
        _run_once()
        return
    # Ctrl-C SIGINTs the whole process group; the parent pool terminates us
    # (and our stdin EOF exits us) — don't dump KeyboardInterrupt tracebacks.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # The command channel is a binary protocol on fd 1. Anything printed to
    # stdout by Python code (e.g. a library emitting a warning via print())
    # would corrupt the protocol, so redirect Python-level stdout to stderr
    # (the server log) and keep the raw buffer solely for protocol traffic.
    global _protocol_out
    _protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr
    # Eagerly import heavy modules before signalling readiness so the parent
    # does not hand us a request while we are still initialising.
    _protocol_out.write(b"\x01")
    _protocol_out.flush()
    _run_loop()


if __name__ == "__main__":
    main()
