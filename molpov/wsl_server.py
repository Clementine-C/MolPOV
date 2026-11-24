#!/usr/bin/env python3
"""Tiny render daemon that runs inside WSL.

The Windows side starts this script once per session:

    wsl -d Ubuntu --exec python3 /mnt/e/.../wsl_server.py 48653

It listens on 127.0.0.1 and accepts one JSON request per TCP connection:

    {"scene": "<pov source>", "args": ["+W480", "+H360", ...]}

and answers with a JSON header line followed by the raw PNG bytes:

    {"ok": true, "ms": 42.0, "size": 12345, "log": "..."}\n<bytes>

Keeping the process alive removes the ~1 s ``wsl.exe`` startup cost from every
preview render, which is what makes interactive parameter tweaking possible.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback

POVRAY_CANDIDATES = (
    "/usr/bin/povray",
    "/usr/local/bin/povray",
)


def find_povray() -> str | None:
    env = os.environ.get("MOLPOV_POVRAY")
    if env and os.path.exists(env):
        return env
    for path in POVRAY_CANDIDATES:
        if os.path.exists(path):
            return path
    for base in os.environ.get("PATH", "").split(":"):
        candidate = os.path.join(base, "povray")
        if os.path.exists(candidate):
            return candidate
    return None


def render(request: dict) -> dict:
    povray = find_povray()
    if not povray:
        return {"ok": False, "error": "povray not found inside WSL"}
    args = [str(a) for a in request.get("args", [])]
    scene = request.get("scene", "")
    fd, out_path = tempfile.mkstemp(suffix=".png", prefix="molpov_out_")
    os.close(fd)
    os.unlink(out_path)
    args.append("+O" + out_path)
    scene_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pov", delete=False, prefix="molpov_scene_"
    )
    data = b""
    log = ""
    try:
        scene_file.write(scene)
        scene_file.close()
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [povray, scene_file.name] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
            log = proc.stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "render timed out"}
        elapsed = (time.perf_counter() - started) * 1000.0
        if not os.path.exists(out_path):
            return {"ok": False, "error": "povray produced no output",
                    "log": log[-4000:], "ms": elapsed}
        with open(out_path, "rb") as handle:
            data = handle.read()
        return {"ok": True, "png": data, "ms": elapsed, "log": log[-4000:]}
    finally:
        for path in (scene_file.name, out_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _read_request(conn: socket.socket) -> dict:
    chunks = bytearray()
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.extend(chunk)
        if b"\n" in chunk:
            break
    line = bytes(chunks).split(b"\n", 1)[0]
    return json.loads(line.decode("utf-8"))


def serve(port: int, idle_timeout: float = 900.0) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(8)
    server.settimeout(idle_timeout)
    print("READY %d" % port, flush=True)
    while True:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            print("IDLE", flush=True)
            return
        try:
            conn.settimeout(900)
            request = _read_request(conn)
            if request.get("op") == "ping":
                conn.sendall(b'{"ok": true, "pong": true}\n')
                continue
            try:
                result = render(request)
            except Exception:  # noqa: BLE001 - report failures to the client
                result = {"ok": False, "error": traceback.format_exc()}
            png = result.pop("png", b"")
            result["size"] = len(png)
            conn.sendall(json.dumps(result).encode("utf-8") + b"\n")
            if png:
                conn.sendall(png)
        except Exception:  # noqa: BLE001 - never kill the daemon
            try:
                conn.sendall(json.dumps({"ok": False,
                                         "error": traceback.format_exc()}).encode() + b"\n")
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 48653)
