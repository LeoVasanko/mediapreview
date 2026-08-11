"""Tests for the preview worker pool resilience.

Regression context: a piped worker stderr that nobody drains used to block
the worker mid-request once the OS pipe buffer filled, and asyncio's
proc.wait() then never resolved even after kill() — wedging one dispatcher
per stuck worker until all preview traffic timed out permanently.
"""

import asyncio
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from mediapreview import pool

FAKE_WORKER = textwrap.dedent(
    """
    import json
    import struct
    import sys

    import blake3


    def read_exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sys.stdin.buffer.read(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf


    sys.stdout.buffer.write(b"\\x01")
    sys.stdout.buffer.flush()
    while True:
        header = sys.stdin.buffer.read(8)
        if not header or len(header) < 8:
            break
        meta_len, payload_len = struct.unpack("<II", header)
        meta = read_exact(meta_len)
        read_exact(payload_len)
        req = json.loads(meta)
        if req["path"].endswith(".block"):
            # Simulate a worker stuck on an undrained stderr pipe:
            # flood stderr past the OS pipe buffer, then never respond.
            import os
            import time

            try:
                os.write(2, b"x" * 10_000_000)
            except OSError:
                pass
            while True:
                time.sleep(3600)
        resp = json.dumps({"ok": True, "mime": "image/avif", "backend": "fake"}).encode()
        payload = b"FAKEIMG"
        packet = struct.pack("<II", len(resp), len(payload)) + resp + payload
        sys.stdout.buffer.write(blake3.blake3(packet).digest())
        sys.stdout.buffer.write(packet)
        sys.stdout.buffer.flush()
    """
)


@pytest.mark.asyncio
async def test_pool_recovers_from_wedged_worker(monkeypatch, tmp_path):
    """A worker wedged on an undrained stderr pipe must not kill the pool.

    Recreates the old production setup (stderr=PIPE, never drained) and
    verifies the request times out, the stuck worker's kill() cannot hang
    the dispatcher, and the pool serves the next request normally.
    """
    monkeypatch.setattr(pool, "PREVIEW_TIMEOUT", 1.0)
    monkeypatch.setattr(pool, "WORKER_KILL_GRACE", 0.5)
    monkeypatch.setattr(pool, "WORKER_RESPAWN_DELAY", 0.05)
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER)

    async def fake_spawn(self):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # Deliberately piped-and-undrained, recreating the old
            # production setup that wedges a worker on stderr writes.
            stderr=asyncio.subprocess.PIPE,
        )
        pool._active_procs.add(proc)
        await asyncio.wait_for(proc.stdout.readexactly(1), timeout=10)
        return pool._PreviewWorker(proc)

    monkeypatch.setattr(pool._PreviewWorkerPool, "_spawn_worker", fake_spawn)

    worker_pool = pool._PreviewWorkerPool(1)
    await worker_pool.start()
    try:
        with pytest.raises(pool.PreviewTimeoutError):
            await worker_pool.run(Path("wedged.block"), 60, 512, 2.0)

        out, resp = await asyncio.wait_for(
            worker_pool.run(Path("ok.jpg"), 60, 512, 2.0), timeout=10
        )
        assert out == b"FAKEIMG"
        assert resp.ok
        assert all(not task.done() for task in worker_pool._dispatchers)
    finally:
        await worker_pool.close()


@pytest.mark.asyncio
async def test_worker_kill_grace_when_wait_hangs(monkeypatch):
    """kill() must return even if asyncio never resolves proc.wait()."""
    monkeypatch.setattr(pool, "WORKER_KILL_GRACE", 0.1)
    proc = Mock()
    proc.returncode = None
    proc.pid = 1234
    never = asyncio.Future()

    async def wait():
        await never

    proc.wait = wait
    worker = pool._PreviewWorker(proc)
    pool._active_procs.add(proc)
    start = time.monotonic()
    await worker.kill()
    assert time.monotonic() - start < 2
    assert proc not in pool._active_procs
    never.cancel()


@pytest.mark.asyncio
async def test_replace_worker_retries_failed_spawn(monkeypatch):
    """A failed replacement spawn must be retried, not silently dropped."""
    monkeypatch.setattr(pool, "WORKER_RESPAWN_DELAY", 0.01)
    worker_pool = pool._PreviewWorkerPool(1)
    old_worker = Mock()
    old_worker.proc = Mock(pid=4321)
    old_worker.kill = AsyncMock()
    attempts = 0

    async def add_worker():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("too many open files")

    worker_pool._add_worker = add_worker
    await worker_pool._replace_worker(old_worker)
    assert attempts == 3


@pytest.mark.asyncio
async def test_dispatch_loop_survives_body_errors():
    """Exceptions escaping a dispatch cycle must not kill the dispatcher."""
    worker_pool = pool._PreviewWorkerPool(1)
    calls = 0

    async def dispatch_one():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    worker_pool._dispatch_one = dispatch_one
    await worker_pool._dispatch_loop()
    assert calls == 2
