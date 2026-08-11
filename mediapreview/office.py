"""OnlyOffice Document Server integration for office document preview.

Provides server-side conversion of office documents to PNG via the
OnlyOffice Document Server /ConvertService.ashx API. The resulting PNG
is passed through pyvips for AVIF compression.

Environment requirements:
    - OnlyOffice Document Server must be running and reachable.
    - If Document Server runs in Docker, the callback host IP must be
      reachable from the container (usually the docker bridge IP).
"""

import asyncio
import json
import logging
import os
import secrets
import socket
import socketserver
import subprocess
import threading
import urllib.error
import urllib.request
from functools import lru_cache, partial
from http.server import SimpleHTTPRequestHandler
from multiprocessing import cpu_count
from pathlib import Path
from time import perf_counter
from urllib.parse import quote

try:
    import httpx
    import jwt
except ImportError:  # pragma: no cover - optional office extra
    httpx = None
    jwt = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


_httpx_client: httpx.AsyncClient | None = None


def _get_onlyoffice_url() -> str:
    return os.environ.get(
        "ONLYOFFICE_URL",
        os.environ.get("ONLYOFFICE_CISTA_URL", "http://localhost:8988"),
    )


def _get_jwt_secret() -> str:
    return os.environ.get("ONLYOFFICE_JWT_SECRET", "")


@lru_cache(maxsize=1)
def _get_callback_host() -> str:
    """Return the host IP that OnlyOffice (usually in Docker) can use to reach us."""
    if host := os.environ.get("ONLYOFFICE_CALLBACK_HOST"):
        return host
    # Try to auto-detect docker bridge IP
    try:
        result = subprocess.run(
            ["/sbin/ip", "-4", "addr", "show", "docker0"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        for line in result.stdout.splitlines():
            if "inet " in line:
                parts = line.strip().split()
                addr_part = parts[1]  # e.g. 172.17.0.1/16
                return addr_part.split("/")[0]
    except Exception:
        logger.debug("Failed to auto-detect docker bridge IP")
    return "127.0.0.1"


def onlyoffice_error_short_text(detail: str) -> str:
    """Short human-readable label for an OnlyOffice failure, for log annotation."""
    if detail.startswith("OnlyOffice conversion error:"):
        code = detail.rsplit(":", 1)[-1].strip()
        return {
            "-8": "onlyoffice jwt error",
            "-4": "onlyoffice input error",
            "-2": "onlyoffice timeout error",
            "-1": "onlyoffice unknown error",
        }.get(code, f"onlyoffice {code} error")
    if "OnlyOffice response did not contain FileUrl" in detail:
        return "onlyoffice no-fileurl error"
    return "onlyoffice error"


# ---------------------------------------------------------------------------
# Async HTTP client
# ---------------------------------------------------------------------------


def get_httpx_client() -> httpx.AsyncClient:
    """Return the shared async HTTP client for OnlyOffice requests."""
    if httpx is None:
        raise ImportError(
            "OnlyOffice integration requires the 'office' extra: pip install mediapreview[office]"
        )
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient()
    return _httpx_client


async def close_oo_client() -> None:
    """Close the shared async HTTP client."""
    global _httpx_client
    if _httpx_client is not None:
        await _httpx_client.aclose()
        _httpx_client = None


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def _probe_status() -> tuple[bool, bool, str | None]:
    """Return (ok, responded, detail) for a lightweight reachability probe."""
    url = _get_onlyoffice_url().rstrip("/") + "/ConvertService.ashx"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        return False, False, None

    if status in (200, 405):
        return True, True, None
    if status >= 500:
        return False, True, f"HTTP {status}"
    return False, True, f"HTTP {status}"


def log_reachable_info() -> None:
    """Log info on success, warning on responded probe errors, silent on no-response."""
    ok, responded, detail = _probe_status()
    if ok:
        logger.info("Using OnlyOffice document server at %s", _get_onlyoffice_url())
    elif responded:
        suffix = f": {detail}" if detail else ""
        logger.warning("OnlyOffice probe failed%s", suffix)


def setup_docker(name: str = "onlyoffice-mediapreview", port: int = 8988) -> int:
    """Build and run the patched OnlyOffice Docker image.

    Uses ONLYOFFICE_JWT_SECRET if set, otherwise generates a random secret.
    The Docker build context ships inside the package at `mediapreview/docker`.
    """
    secret = _get_jwt_secret() or secrets.token_hex(16)
    docker_dir = Path(__file__).parent / "docker"
    if not docker_dir.is_dir():
        raise FileNotFoundError(
            f"Docker files not found at {docker_dir}. Is the package installed correctly?"
        )

    logger.info("Building OnlyOffice image")
    build_cmd = ["docker", "build", "-t", name, str(docker_dir)]
    logger.info("%s", " ".join(build_cmd))
    result = subprocess.run(build_cmd, check=False, shell=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError("Failed to build OnlyOffice image")

    logger.info("Starting OnlyOffice container")
    run_cmd = [
        "docker",
        "run",
        "-d",
        "-p",
        f"{port}:80",
        "-e",
        f"JWT_SECRET={secret}",
        "-e",
        "WORKERS=8",
        "--name",
        name,
        "--restart",
        "unless-stopped",
        name,
    ]
    logger.info("%s", " ".join(run_cmd))
    result = subprocess.run(run_cmd, check=False, shell=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError("Failed to start OnlyOffice container")
    logger.info("OnlyOffice is running on http://localhost:%d", port)
    return 0


async def is_available_async(request_timeout: float = 2.0) -> bool:
    """Return True if the configured OnlyOffice Document Server is reachable."""
    if httpx is None:
        raise ImportError(
            "OnlyOffice integration requires the 'office' extra: pip install mediapreview[office]"
        )
    url = _get_onlyoffice_url().rstrip("/") + "/ConvertService.ashx"
    client = get_httpx_client()
    try:
        response = await client.get(url, timeout=request_timeout)
    except Exception:
        return False
    else:
        return response.status_code in (200, 405)


_oo_available_cache: tuple[bool, float] | None = None
OO_AVAILABILITY_CACHE_TTL = 30.0


async def is_available_cached() -> bool:
    """Return cached OnlyOffice availability, refreshed every 30 seconds.

    State transitions are logged, so an unreachable server is reported once
    instead of on every preview attempt.
    """
    global _oo_available_cache
    now = perf_counter()
    if _oo_available_cache is not None:
        result, timestamp = _oo_available_cache
        if now - timestamp < OO_AVAILABILITY_CACHE_TTL:
            return result
    result = await is_available_async()
    if _oo_available_cache is None or _oo_available_cache[0] != result:
        if result:
            logger.info(
                "OnlyOffice document server available at %s", _get_onlyoffice_url()
            )
        else:
            logger.warning(
                "OnlyOffice document server not reachable at %s", _get_onlyoffice_url()
            )
    _oo_available_cache = (result, now)
    return result


# ---------------------------------------------------------------------------
# Temporary HTTP server so OnlyOffice can download the file
# ---------------------------------------------------------------------------


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        pass


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))  # noqa: S104
        return s.getsockname()[1]


def _serve_file_temporarily(file_path: Path):
    """Start a temporary HTTP server for *file_path* and return (url, server)."""
    directory = str(file_path.parent)
    filename = file_path.name
    port = _get_free_port()

    handler = partial(_QuietHandler, directory=directory)
    httpd = socketserver.TCPServer(("0.0.0.0", port), handler)  # noqa: S104
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    host = _get_callback_host()
    url = f"http://{host}:{port}/{quote(filename)}"
    return url, httpd


# ---------------------------------------------------------------------------
# OnlyOffice conversion client
# ---------------------------------------------------------------------------


def _build_jwt_token(payload: dict) -> str | None:
    secret = _get_jwt_secret()
    if not secret:
        return None
    return jwt.encode(payload, secret, algorithm="HS256")


async def convert_to_png_async(file_path: Path, request_timeout: float = 5.0) -> bytes:
    """Convert *file_path* to PNG using OnlyOffice Document Server (async).

    Returns the PNG bytes. Raises RuntimeError on failure.
    """
    if httpx is None or jwt is None:
        raise ImportError(
            "OnlyOffice integration requires the 'office' extra: pip install mediapreview[office]"
        )
    oo_url = _get_onlyoffice_url().rstrip("/")
    convert_url = f"{oo_url}/ConvertService.ashx"
    client = get_httpx_client()

    # Start temporary HTTP server so OnlyOffice can fetch the file
    doc_url, httpd = await asyncio.to_thread(_serve_file_temporarily, file_path)
    try:
        suffix = file_path.suffix.lstrip(".").lower()
        payload = {
            "async": False,
            "filetype": suffix,
            "key": f"mediapreview_{(await asyncio.to_thread(file_path.stat)).st_mtime_ns}",
            "outputtype": "png",
            "title": file_path.name,
            "url": doc_url,
        }

        headers = {"Content-Type": "application/json"}
        token = _build_jwt_token(payload)
        if token:
            # Conversion API expects JWT in request body when token checks are enabled.
            payload["token"] = token
            headers["Authorization"] = token

        t_start = perf_counter()
        response = await client.post(
            convert_url,
            content=json.dumps(payload).encode(),
            headers=headers,
            timeout=request_timeout,
        )
        response.raise_for_status()
        body = response.content
        t_end = perf_counter()

        # Parse XML response
        text = body.decode("utf-8", errors="replace")
        if "<Error>" in text:
            code = "unknown"
            if "<Error>" in text and "</Error>" in text:
                code = text.split("<Error>")[1].split("</Error>")[0]
            raise RuntimeError(f"OnlyOffice conversion error: {code}")

        if "<FileUrl>" not in text:
            raise RuntimeError("OnlyOffice response did not contain FileUrl")

        file_url = text.split("<FileUrl>")[1].split("</FileUrl>")[0]
        file_url = file_url.replace("&amp;", "&")

        logger.debug("OnlyOffice converted in %.2fs: %s", t_end - t_start, file_url)

        # Download converted PNG
        png_response = await client.get(file_url, timeout=request_timeout)
        png_response.raise_for_status()
        return png_response.content
    finally:
        await asyncio.to_thread(httpd.shutdown)


# ---------------------------------------------------------------------------
# Conversion manager (pulled from the preview orchestrator)
# ---------------------------------------------------------------------------

# Max concurrent OnlyOffice conversion requests. OO has its own queue;
# we must not flood it. This is intentionally small.
OO_MAX_CONCURRENT = max(2, min(8, cpu_count()))


class OOConversionManager:
    """Manages async OnlyOffice conversions with deduplication and concurrency limits."""

    def __init__(self, max_concurrent: int = OO_MAX_CONCURRENT):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._in_flight: dict[str, asyncio.Future[bytes]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    async def convert(self, filepath: Path) -> bytes:
        """Return PNG bytes for *filepath*, deduplicating concurrent requests."""
        if not await is_available_cached():
            raise RuntimeError("OnlyOffice server not reachable")
        stat = await asyncio.to_thread(filepath.stat)
        key = f"{filepath}:{stat.st_mtime_ns}"

        async with self._lock:
            if key in self._in_flight:
                future = self._in_flight[key]
            else:
                future = asyncio.get_running_loop().create_future()
                self._in_flight[key] = future
                task = asyncio.create_task(self._do_convert(filepath, key, future))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        return await future

    async def _do_convert(
        self, filepath: Path, key: str, future: asyncio.Future[bytes]
    ) -> None:
        try:
            async with self._semaphore:
                png_bytes = await convert_to_png_async(filepath, request_timeout=5.0)
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            async with self._lock:
                self._in_flight.pop(key, None)
        else:
            if not future.done():
                future.set_result(png_bytes)
            async with self._lock:
                self._in_flight.pop(key, None)


_oo_manager: OOConversionManager | None = None


def get_oo_manager() -> OOConversionManager:
    """Return the singleton OOConversionManager."""
    global _oo_manager
    if _oo_manager is None:
        _oo_manager = OOConversionManager(max_concurrent=OO_MAX_CONCURRENT)
    return _oo_manager
