"""Async preview worker pool framework."""

import asyncio
import contextlib
import logging
import os
import pickle
import signal
import struct
import sys
from multiprocessing import cpu_count
from pathlib import Path
from time import perf_counter

import msgspec

try:
    from blake3 import blake3
except ImportError as e:  # pragma: no cover - optional worker extra
    raise ImportError(
        "The worker pool requires the 'worker' extra: pip install mediapreview[worker]"
    ) from e

from mediapreview.exceptions import (
    PreviewError,
    PreviewTimeoutError,
    backend_error,
    preview_cancelled_error,
    preview_timeout_error,
)
from mediapreview.formats import (
    expected_backend as _expected_preview_backend,
)
from mediapreview.formats import (
    is_previewable_path,
)
from mediapreview.formats import (
    preview_job_priority as _preview_job_priority,
)
from mediapreview.office import get_oo_manager
from mediapreview.protocol import PreviewRequest, PreviewResponse

__all__ = [
    "PREVIEW_TIMEOUT",
    "PreviewError",
    "PreviewTimeoutError",
    "generate_office_preview",
    "is_previewable_path",
    "run_preview",
    "shutdown_preview_workers",
    "start_preview_workers",
]

logger = logging.getLogger(__name__)


class WorkerChecksumError(Exception):
    """Raised when worker response checksum does not match the packet."""


class WorkerProtocolError(Exception):
    """Raised when worker response packet is malformed."""


def _reraise_worker_error(resp: PreviewResponse, payload: bytes) -> None:
    """Re-raise the exception the worker sent back in the payload, if any.

    The payload is pickled by our own worker processes (same trust domain), so
    the original exception type arrives intact on the caller side. Falls back
    to a plain PreviewBackendError built from the error message.
    """
    if payload:
        exc = pickle.loads(payload)  # noqa: S301 - trusted: our own workers
        if isinstance(exc, Exception):
            raise exc
    raise backend_error(resp.backend or "unknown", resp.error or "preview worker error")


PREVIEW_TIMEOUT = 10.0  # seconds until preview subprocess is killed
PREVIEW_WORKERS = max(2, min(8, cpu_count()))
WORKER_KILL_GRACE = 5.0  # max seconds to wait for a killed worker to be reaped
WORKER_RESPAWN_DELAY = 1.0  # initial delay before retrying a failed worker spawn
WORKER_RESPAWN_DELAY_MAX = 30.0
WORKER_CHECKSUM_BYTES = 32
WORKER_MAX_JSON_BYTES = 1_000_000

_active_procs: set[asyncio.subprocess.Process] = set()
_preview_pool = None
_preview_pool_lock = asyncio.Lock()
_pool_stopped = False


class _PreviewWorker:
    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc

    async def request(
        self,
        filepath,
        quality: int,
        maxsize: int,
        maxzoom: float,
        data: bytes | None = None,
    ):
        if self.proc.returncode is not None:
            raise WorkerProtocolError("worker already exited")
        if self.proc.stdin is None or self.proc.stdout is None:
            raise WorkerProtocolError("worker streams not available")

        meta = msgspec.json.encode(
            PreviewRequest(
                path=str(filepath),
                quality=quality,
                maxsize=maxsize,
                maxzoom=maxzoom,
            )
        )
        payload = data or b""
        packet = struct.pack("<II", len(meta), len(payload)) + meta + payload
        self.proc.stdin.write(packet)
        await self.proc.stdin.drain()

        checksum = await self.proc.stdout.readexactly(WORKER_CHECKSUM_BYTES)
        header = await self.proc.stdout.readexactly(8)
        json_size, data_size = struct.unpack("<II", header)
        if json_size > WORKER_MAX_JSON_BYTES:
            raise WorkerProtocolError(f"worker JSON too large: {json_size}")
        meta_raw = await self.proc.stdout.readexactly(json_size)
        payload = await self.proc.stdout.readexactly(data_size)
        packet = header + meta_raw + payload
        if blake3(packet).digest() != checksum:
            raise WorkerChecksumError("worker checksum mismatch")

        resp = msgspec.json.decode(meta_raw, type=PreviewResponse)
        if not resp.ok:
            _reraise_worker_error(resp, payload)
        return payload or None, resp

    async def kill(self) -> None:
        try:
            if self.proc.returncode is None:
                # Safe to hard-kill: the worker is stateless per request.
                # Kill the whole process group (worker is the group leader,
                # spawned with start_new_session) so that an in-flight ffmpeg
                # grandchild cannot be orphaned by the worker's SIGKILL.
                # proc.wait() must not be awaited unaided: if a pipe
                # transport is flow-control paused (e.g. an undrained stderr
                # pipe), asyncio may never resolve wait() even after SIGKILL,
                # which would permanently wedge the calling dispatcher.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=WORKER_KILL_GRACE)
                except TimeoutError:
                    logger.exception(
                        "Preview worker pid=%s not reaped within %ds of kill",
                        self.proc.pid,
                        int(WORKER_KILL_GRACE),
                    )
        finally:
            _active_procs.discard(self.proc)


class _PreviewWorkerPool:
    def __init__(self, size: int):
        self.size = size
        self._idle: asyncio.Queue[_PreviewWorker] = asyncio.Queue()
        self._pending: asyncio.PriorityQueue[tuple[int, int, asyncio.Future, tuple]] = (
            asyncio.PriorityQueue()
        )
        self._workers: set[_PreviewWorker] = set()
        self._dispatchers: list[asyncio.Task] = []
        self._in_flight: set[asyncio.Future] = set()
        self._seq = 0
        self._closed = False

    async def _spawn_worker(self) -> _PreviewWorker:
        # stderr is inherited, not piped: a piped stderr that nobody drains
        # eventually fills its OS buffer, blocking the worker mid-request,
        # and its flow-control-paused transport makes proc.wait() hang even
        # after kill() — together this used to permanently wedge the pool.
        # Inheriting sends worker diagnostics straight to the server log.
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "mediapreview.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            # Own process group so kill() can SIGKILL the worker together with
            # any grandchild (e.g. ffmpeg) it may have spawned.
            start_new_session=True,
        )
        _active_procs.add(proc)
        try:
            ready = await asyncio.wait_for(proc.stdout.readexactly(1), timeout=30.0)
        except TimeoutError as err:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                await proc.wait()
            raise WorkerProtocolError(
                "preview worker failed to become ready"
                " (worker stderr goes to the server log)"
            ) from err
        except asyncio.IncompleteReadError as err:
            raise WorkerProtocolError(
                "preview worker exited before signalling readiness"
                " (worker stderr goes to the server log)"
            ) from err
        if ready != b"\x01":
            raise WorkerProtocolError(f"preview worker ready signal invalid: {ready!r}")
        return _PreviewWorker(proc)

    async def _add_worker(self) -> None:
        worker = await self._spawn_worker()
        self._workers.add(worker)
        await self._idle.put(worker)

    async def _replace_worker(self, worker: _PreviewWorker) -> None:
        self._workers.discard(worker)
        try:
            await worker.kill()
        except Exception:
            logger.exception("Failed to kill preview worker pid=%s", worker.proc.pid)
        # Keep retrying until a replacement is up: a pool that silently
        # shrinks degrades all preview traffic to timeouts.
        delay = WORKER_RESPAWN_DELAY
        while not self._closed:
            try:
                await self._add_worker()
            except Exception:
                logger.exception(
                    "Failed to replace preview worker (pool %d/%d); retrying in %ds",
                    len(self._workers),
                    self.size,
                    int(delay),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, WORKER_RESPAWN_DELAY_MAX)
            else:
                return

    async def _dispatch_loop(self) -> None:
        # Nothing may escape the loop body: a dispatcher that dies silently
        # permanently shrinks pool capacity and degrades all preview
        # traffic to timeouts.
        while True:
            try:
                await self._dispatch_one()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Preview dispatcher error; continuing")

    async def _dispatch_one(self) -> None:
        _priority, _seq, future, args = await self._pending.get()

        if future.cancelled():
            return

        try:
            worker = await asyncio.wait_for(self._idle.get(), timeout=PREVIEW_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "Preview worker unavailable (%ds) for %s",
                int(PREVIEW_TIMEOUT),
                args[0].name,
            )
            if not future.done():
                future.set_exception(
                    preview_timeout_error(
                        _expected_preview_backend(args[0]),
                        PREVIEW_TIMEOUT,
                    )
                )
            return

        filepath = args[0]
        replace = False
        try:
            out, resp = await asyncio.wait_for(
                worker.request(*args),
                timeout=PREVIEW_TIMEOUT,
            )
            if not future.done():
                future.set_result((out, resp))
        except TimeoutError:
            replace = True
            logger.warning(
                "Preview worker pid=%s timed out (%ds) on %s; replacing it",
                worker.proc.pid,
                int(PREVIEW_TIMEOUT),
                filepath.name,
            )
            if not future.done():
                future.set_exception(
                    preview_timeout_error(
                        _expected_preview_backend(filepath),
                        PREVIEW_TIMEOUT,
                    )
                )
        except WorkerChecksumError:
            replace = True
            logger.exception(
                "Preview checksum mismatch for %s (worker pid=%s); replacing it",
                filepath.name,
                worker.proc.pid,
            )
            if not future.done():
                future.set_exception(
                    backend_error(
                        _expected_preview_backend(filepath),
                        f"worker checksum mismatch for {filepath.name}",
                    )
                )
        except PreviewError as e:
            if not future.done():
                future.set_exception(e)
        except (
            WorkerProtocolError,
            asyncio.IncompleteReadError,
            BrokenPipeError,
            ConnectionResetError,
            OSError,
            ValueError,
            msgspec.DecodeError,
        ) as e:
            replace = True
            logger.warning(
                "Preview worker pid=%s protocol failure for %s: %s",
                worker.proc.pid,
                filepath.name,
                e,
            )
            if not future.done():
                future.set_exception(
                    backend_error(
                        _expected_preview_backend(filepath),
                        f"worker protocol failure for {filepath.name}: {e}",
                    )
                )
        except Exception:
            replace = True
            logger.exception("Unexpected preview worker error for %s", filepath.name)
            if not future.done():
                future.set_exception(
                    backend_error(
                        _expected_preview_backend(filepath),
                        f"unexpected worker error for {filepath.name}",
                    )
                )
        finally:
            if replace:
                await self._replace_worker(worker)
            elif worker.proc.returncode is None:
                await self._idle.put(worker)
            else:
                await self._replace_worker(worker)

    async def start(self) -> None:
        workers = await asyncio.gather(
            *(self._spawn_worker() for _ in range(self.size))
        )
        for worker in workers:
            self._workers.add(worker)
            await self._idle.put(worker)
        for _ in range(self.size):
            self._dispatchers.append(asyncio.create_task(self._dispatch_loop()))

    async def run(
        self,
        filepath,
        quality: int,
        maxsize: int,
        maxzoom: float,
        data: bytes | None = None,
    ):
        if self._closed:
            raise preview_cancelled_error("preview worker pool closed")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._in_flight.add(future)
        self._seq += 1
        await self._pending.put(
            (
                _preview_job_priority(filepath),
                self._seq,
                future,
                (filepath, quality, maxsize, maxzoom, data),
            )
        )
        try:
            return await future
        finally:
            self._in_flight.discard(future)

    async def close(self) -> None:
        self._closed = True
        for task in self._dispatchers:
            task.cancel()
        if self._dispatchers:
            await asyncio.gather(*self._dispatchers, return_exceptions=True)
        self._dispatchers.clear()
        workers = list(self._workers)
        self._workers.clear()
        # Fail every future still waiting on a result — pending and in-flight
        # alike — so request handlers finish immediately instead of waiting
        # out their timeouts during server shutdown.
        for future in list(self._in_flight):
            if not future.done():
                future.set_exception(preview_cancelled_error("pool closed"))
        while not self._pending.empty():
            try:
                _priority, _seq, future, _args = self._pending.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(preview_cancelled_error("pool closed"))
        while not self._idle.empty():
            try:
                self._idle.get_nowait()
            except asyncio.QueueEmpty:
                break
        await asyncio.gather(
            *(worker.kill() for worker in workers), return_exceptions=True
        )


async def start_preview_workers() -> None:
    """Warm up persistent preview workers."""
    global _preview_pool
    if _preview_pool is not None or _pool_stopped:
        return
    async with _preview_pool_lock:
        if _preview_pool is not None or _pool_stopped:
            return
        pool = _PreviewWorkerPool(PREVIEW_WORKERS)
        await pool.start()
        _preview_pool = pool
        logger.info("Started %d persistent preview workers", PREVIEW_WORKERS)


async def shutdown_preview_workers() -> None:
    """Kill persistent preview workers."""
    global _preview_pool, _pool_stopped
    _pool_stopped = True
    async with _preview_pool_lock:
        pool = _preview_pool
        _preview_pool = None
    if pool is not None:
        await pool.close()
    if not _active_procs:
        return
    for proc in list(_active_procs):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    await asyncio.gather(
        *(proc.wait() for proc in list(_active_procs)), return_exceptions=True
    )
    _active_procs.clear()


async def generate_office_preview(
    filepath: Path, quality: int, maxsize: int, maxzoom: float
) -> tuple[bytes | None, PreviewResponse | None]:
    """Generate a preview for an office file using OnlyOffice + worker AVIF conversion.

    Raises:
        OnlyOfficeError: If the OnlyOffice Document Server cannot convert the file.
    """
    manager = get_oo_manager()
    t_oo_start = perf_counter()
    png_bytes = await manager.convert(filepath)
    t_oo_end = perf_counter()

    img, resp = await run_preview(filepath, quality, maxsize, maxzoom, data=png_bytes)

    if resp is not None:
        resp.backend = "onlyoffice+" + (resp.backend or "pyvips")
        if resp.timings:
            resp.timings = [round((t_oo_end - t_oo_start) * 1000, 1), *resp.timings]
    return img, resp


async def run_preview(
    filepath, quality: int, maxsize: int, maxzoom: float, data: bytes | None = None
) -> tuple[bytes | None, PreviewResponse | None]:
    """Run preview request in a persistent worker process."""
    await start_preview_workers()
    if _preview_pool is None:
        raise preview_cancelled_error("preview worker pool closed")
    return await _preview_pool.run(filepath, quality, maxsize, maxzoom, data)
