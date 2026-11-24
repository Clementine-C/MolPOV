"""POV-Ray back ends: native console build, the WSL daemon, or the GUI engine.

The renderer is deliberately synchronous and thread agnostic; the Qt layer
runs it inside a worker thread and cancels by calling :meth:`Renderer.cancel`.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field, replace
from typing import List, Optional, Sequence, Tuple

from . import sdl

PREVIEW_WIDTH = 480
FINAL_WIDTH = 1280


def wsl_path(win_path: str) -> str:
    """``E:\\a\\b`` -> ``/mnt/e/a/b`` (used for the daemon script)."""
    drive, rest = os.path.splitdrive(os.path.abspath(win_path))
    rest = rest.replace("\\", "/")
    return f"/mnt/{drive[0].lower()}{rest}"


@dataclass
class RenderOutcome:
    ok: bool
    png: bytes = b""
    elapsed_ms: float = 0.0
    backend: str = ""
    log: str = ""
    error: str = ""

    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.png)


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# back ends
# --------------------------------------------------------------------------

def _candidate_roots() -> List[str]:
    roots = [
        os.environ.get("MOLPOV_POVRAY_DIR", ""),
        r"D:\POV-Ray",
        r"C:\POV-Ray",
        r"C:\Program Files\POV-Ray",
        r"C:\Program Files (x86)\POV-Ray",
    ]
    return [root for root in roots if root and os.path.isdir(root)]


def find_native_povray() -> Optional[str]:
    """A real console build (``povray.exe``), which starts in milliseconds."""
    env = os.environ.get("MOLPOV_POVRAY")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("povray")
    if found:
        return found
    for root in _candidate_roots():
        for version in sorted(os.listdir(root), reverse=True):
            for rel in ("povray.exe", r"bin\povray.exe"):
                path = os.path.join(root, version, rel)
                if os.path.isfile(path):
                    return path
    return None


def find_gui_engine() -> Optional[str]:
    """The Windows GUI engine, usable in batch mode via ``/RENDER``."""
    for root in _candidate_roots():
        for version in sorted(os.listdir(root), reverse=True):
            for name in ("pvengine64.exe", "pvengine.exe"):
                path = os.path.join(root, version, "bin", name)
                if os.path.isfile(path):
                    return path
    return None


def list_wsl_distros() -> List[str]:
    try:
        proc = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = proc.stdout.decode("utf-16-le", "ignore") or proc.stdout.decode("utf-8", "ignore")
    return [line.strip() for line in text.splitlines() if line.strip()]


class WslDaemon:
    """Persistent ``povray`` server inside a WSL distribution."""

    def __init__(self, distro: str, script: str, port: int = 0):
        self.distro = distro
        self.script = script
        self.port = port or self._free_port()
        self._proc: Optional[subprocess.Popen] = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 20.0) -> None:
        if self.running:
            return
        last_error: Optional[str] = None
        for attempt in (1, 2):
            try:
                self._spawn(timeout)
                return
            except RenderError as exc:
                last_error = str(exc)
                self.stop()
                self._kill_stale()
        raise RenderError(last_error or "could not start WSL render daemon")

    def _kill_stale(self) -> None:
        try:
            subprocess.run(
                ["wsl.exe", "-d", self.distro, "--exec", "pkill", "-f",
                 "molpov/wsl_server.py"],
                capture_output=True, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _spawn(self, timeout: float) -> None:
        cmd = [
            "wsl.exe", "-d", self.distro, "--exec", "python3",
            wsl_path(self.script), str(self.port),
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.time() + timeout
        assert self._proc.stdout is not None
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if line.startswith("READY"):
                return
            if not line:
                if self._proc.poll() is not None:
                    raise RenderError("WSL render daemon exited during startup")
                time.sleep(0.05)
        raise RenderError("WSL render daemon did not start in time")

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
        except OSError:
            pass
        self._proc = None

    def request(self, payload: dict, timeout: float = 900.0) -> Tuple[dict, bytes]:
        self.start()
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            stream = sock.makefile("rb")
            header_line = stream.readline()
            if not header_line:
                raise RenderError("empty reply from render daemon")
            header = json.loads(header_line.decode("utf-8"))
            data = b""
            size = int(header.get("size") or 0)
            if size:
                data = stream.read(size)
        return header, data


# --------------------------------------------------------------------------
# renderer
# --------------------------------------------------------------------------

@dataclass
class Quality:
    label: str
    width: int
    height: int
    antialias: float = 0.0      # 0 disables AA
    aa_method: int = 2
    radiosity: Optional[bool] = None  # None = use scene value
    extra: Tuple[str, ...] = ()       # additional command line options
    full: bool = False                # Avogadro's complete switch set

    # Avogadro 1.x POV-Ray dialog switches, verbatim:
    #   +I<scene> +O<image> +W.. +H.. +V +D +FN +Q9 +P +UD +UL +UV +A +AM2 +UA
    AVOGADRO_SWITCHES = ("+V", "+D", "+FN", "+Q9", "+P", "+UD", "+UL", "+UV",
                         "+A", "+AM2", "+UA")

    def cli_args(self, display: bool = False) -> List[str]:
        if self.full:
            args = [f"+W{self.width}", f"+H{self.height}", "+FN", "+Q9",
                    "+A", "+AM2", "+UA"]
            if display:
                args += ["+V", "+D", "+P", "+UD", "+UL", "+UV"]
            return args + list(self.extra)
        args = [f"+W{self.width}", f"+H{self.height}"]
        if self.antialias > 0:
            args += [f"+A{self.antialias}", f"+AM{self.aa_method}"]
        else:
            args += ["-A"]
        if self.radiosity is not None:
            args.append(f"Declare=RADIOSITY={1 if self.radiosity else 0}")
        args += list(self.extra)
        args += ["+FN"]
        return args


def preview_quality(width: int = PREVIEW_WIDTH, aspect: float = 4 / 3) -> Quality:
    """Fast settings: no antialiasing, radiosity forced off (it is the main cost)."""
    width = max(160, int(width))
    return Quality("preview", width, max(120, int(round(width / max(0.2, aspect)))),
                   0.0, radiosity=False)


def final_quality(width: int = FINAL_WIDTH, aspect: float = 4 / 3,
                  antialias: float = 0.25, quality: int = 9) -> Quality:
    width = max(320, int(width))
    extra = (f"+Q{quality}",) if 0 <= quality <= 11 else ()
    return Quality("final", width,
                   max(240, int(round(width / max(0.2, aspect)))),
                   antialias, extra=extra)


def full_command_quality(width: int = 1024, height: int = 768) -> Quality:
    """Exactly Avogadro's command line: +Q9 +A +AM2 +UA (and display switches)."""
    return Quality("full", max(160, int(width)), max(120, int(height)),
                   full=True)


def _dismiss_pov_dialogs(pid: int) -> int:
    """Close the "internal editor could not be loaded" prompt of the GUI build.

    The AGPL Windows build has no editor DLL, so it asks on every launch; in
    batch mode it must be answered or the render never starts.
    """
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    clicked = 0
    dialogs: List[int] = []
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buffer = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buffer, 64)
            if buffer.value == "#32770":
                dialogs.append(hwnd)
        return True

    user32.EnumWindows(callback(collect), 0)
    if os.environ.get("MOLPOV_DEBUG"):
        print(f"[dismiss] pid={pid} dialogs={dialogs}", flush=True)
    for dialog in dialogs:
        def press(hwnd, _):
            nonlocal clicked
            if user32.GetDlgCtrlID(hwnd) == 7:  # "No"
                user32.SendMessageW(hwnd, 0x00F5, 0, 0)  # BM_CLICK
                clicked += 1
                return False
            return True

        user32.EnumChildWindows(dialog, callback(press), 0)
    return clicked


def _hide_pov_windows(pid: int) -> None:
    """Hide POV-Ray's own windows (the full command line passes ``+D``)."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def hide(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buffer = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buffer, 64)
            if buffer.value != "#32770":  # keep dialogs visible for dismissal
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
        return True

    user32.EnumWindows(callback(hide), 0)


class Renderer:
    """Picks the best available back end and renders scenes on demand."""

    def __init__(self, script_path: str, distro: Optional[str] = None,
                 prefer: Optional[str] = None, exe_path: Optional[str] = None):
        self.script_path = script_path
        self.explicit_exe: Optional[str] = (
            exe_path if exe_path and os.path.isfile(exe_path) else None)
        self.native = find_native_povray()
        self.gui = find_gui_engine()
        env_backend = os.environ.get("MOLPOV_BACKEND")
        self.prefer = prefer or (env_backend if env_backend in
                                 ("native", "gui", "wsl") else None)
        if self.explicit_exe:
            # A user-chosen executable wins over auto-detection.
            if os.path.basename(self.explicit_exe).lower().startswith("pvengine"):
                self.gui = self.explicit_exe
                self.native = None
                self.prefer = "gui"
            else:
                self.native = self.explicit_exe
                self.gui = None
                self.prefer = "native"
        self.distro: Optional[str] = distro or os.environ.get("MOLPOV_WSL_DISTRO")
        self._distros_checked = bool(self.distro)
        self.daemon: Optional[WslDaemon] = None
        self._gui_proc: Optional[subprocess.Popen] = None
        self._native_proc: Optional[subprocess.Popen] = None
        self._cancelled = False
        self.last_backend = ""

    def wsl_distro(self) -> Optional[str]:
        if not self._distros_checked:
            self._distros_checked = True
            if not self.distro:
                distros = list_wsl_distros()
                self.distro = distros[0] if distros else None
        return self.distro

    # ------------------------------------------------------------ discovery
    def backend_name(self) -> str:
        if self.prefer:
            return self.prefer
        if self.native:
            return "native"
        if self.gui:
            return "gui"
        if self.wsl_distro():
            return "wsl"
        return "none"

    def describe(self) -> str:
        return {
            "native": f"POV-Ray console ({self.native})",
            "wsl": f"POV-Ray in WSL ({self.wsl_distro()})",
            "gui": f"POV-Ray for Windows ({self.gui})",
            "none": "not found",
        }[self.backend_name()]

    # -------------------------------------------------------------- render
    def render(self, structure, params: "sdl.SceneParams", quality: Quality) -> RenderOutcome:
        self._cancelled = False
        scene_params = replace(params, width=quality.width, height=quality.height)
        text = sdl.generate_pov(structure, scene_params)
        if params.alpha and "+UA" not in quality.extra:
            quality = replace(quality, extra=tuple(quality.extra) + ("+UA",))
        if params.transparency > 0 and quality.label == "preview":
            # Semi-transparent scenes multiply ray casts; keep previews snappy.
            quality = replace(quality, extra=tuple(quality.extra)
                              + ("Declare=MAXTRACE=2",))
        if quality.full and self.backend_name() != "gui":
            # +Q9/+D/+P are Windows-GUI switches; console back ends use plain AA.
            quality = replace(quality, full=False, antialias=0.3)
        started = time.perf_counter()
        try:
            if self.backend_name() == "native":
                outcome = self._render_native(text, quality)
            elif self.backend_name() == "wsl":
                outcome = self._render_wsl(text, quality)
            elif self.backend_name() == "gui":
                outcome = self._render_gui(text, quality)
            else:
                return RenderOutcome(False, error="no POV-Ray back end available")
        except Exception as exc:  # noqa: BLE001 - surface the message in the UI
            return RenderOutcome(False, error=str(exc), backend=self.backend_name(),
                                 elapsed_ms=(time.perf_counter() - started) * 1000)
        outcome.elapsed_ms = outcome.elapsed_ms or (time.perf_counter() - started) * 1000
        outcome.backend = self.backend_name()
        return outcome

    # Avogadro 1.x POV-Ray dialog switches, verbatim:
    #   +I<scene> +O<image> +W.. +H.. +V +D +FN +Q9 +P +UD +UL +UV +A +AM2 +UA
    AVOGADRO_SWITCHES = ("+V", "+D", "+FN", "+Q9", "+P", "+UD", "+UL", "+UV",
                         "+A", "+AM2", "+UA")

    def render_pov_file(self, pov_path: str, width: int = 1024,
                        height: int = 768, alpha: bool = True) -> RenderOutcome:
        """Render an existing ``.pov`` exactly like Avogadro's dialog does."""
        try:
            with open(pov_path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            return RenderOutcome(False, error=str(exc))
        return self.render_pov_text(text, width, height, alpha)

    def render_pov_text(self, text: str, width: int = 1024,
                        height: int = 768, alpha: bool = True) -> RenderOutcome:
        """Render raw SDL text with Avogadro's complete switch set.

        POV-Ray for Windows only reads/writes inside its permitted paths, so the
        scene is written to a temp directory first; the rendered PNG is read
        back from there.  The GUI engine keeps running after a frame
        (``+D``/``+P``), so completion is detected from the PNG becoming
        size-stable.
        """
        import tempfile

        if not self.gui:
            return RenderOutcome(False, error="no GUI engine")
        started = time.perf_counter()
        tmp = tempfile.mkdtemp(prefix="molpov_pov_")
        scene = os.path.join(tmp, "scene.pov")
        out = os.path.join(tmp, "render.png")
        try:
            with open(scene, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            return RenderOutcome(False, error=str(exc))
        switches = [flag for flag in self.AVOGADRO_SWITCHES
                    if alpha or flag != "+UA"]
        args = ([self.gui, "+I" + scene, "+O" + out,
                 f"+W{width}", f"+H{height}"] + switches)
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._gui_proc = proc
        deadline = time.time() + 900
        last_size = -1
        stable_since = None
        data = b""
        try:
            while time.time() < deadline:
                _dismiss_pov_dialogs(proc.pid)
                if os.path.exists(out):
                    size = os.path.getsize(out)
                    if size > 0:
                        if size == last_size:
                            if stable_since is None:
                                stable_since = time.time()
                            elif time.time() - stable_since > 0.6:
                                # PNG is complete only once the IEND chunk is there.
                                with open(out, "rb") as probe:
                                    probe.seek(max(0, size - 8))
                                    tail = probe.read()
                                if tail.endswith(b"IEND\xaeB`\x82"):
                                    break
                        else:
                            stable_since = None
                            last_size = size
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if os.path.exists(out):
                with open(out, "rb") as handle:
                    data = handle.read()
        finally:
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._gui_proc = None
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        if not data:
            return RenderOutcome(False, error="POV-Ray produced no output")
        if not data.startswith(b"\x89PNG"):
            return RenderOutcome(False, error="POV-Ray wrote an invalid PNG")
        return RenderOutcome(True, data, backend="gui",
                             elapsed_ms=(time.perf_counter() - started) * 1000,
                             log=" ".join(args))

    def _render_native(self, text: str, quality: Quality) -> RenderOutcome:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="molpov_") as tmp:
            scene = os.path.join(tmp, "scene.pov")
            out = os.path.join(tmp, "out.png")
            with open(scene, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            cmd = [self.native, scene] + quality.cli_args() + ["+O" + out, "-D"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            self._native_proc = proc
            deadline = time.time() + 900
            try:
                while proc.poll() is None and time.time() < deadline:
                    if self._cancelled:
                        proc.kill()
                    time.sleep(0.05)
            finally:
                if proc.poll() is None:
                    proc.kill()
                self._native_proc = None
            log = b""
            if proc.stdout is not None:
                log = proc.stdout.read() or b""
            text_log = log.decode("utf-8", "replace")
            if self._cancelled:
                return RenderOutcome(False, error="cancelled", log=text_log)
            if not os.path.exists(out):
                return RenderOutcome(False, log=text_log,
                                     error="POV-Ray produced no output")
            with open(out, "rb") as handle:
                data = handle.read()
            return RenderOutcome(True, data, log=text_log)

    def _render_wsl(self, text: str, quality: Quality) -> RenderOutcome:
        payload = {"scene": text, "args": quality.cli_args()}
        if self.daemon is None:
            distro = self.wsl_distro()
            if not distro:
                return RenderOutcome(False, error="no WSL distribution available")
            self.daemon = WslDaemon(distro, self.script_path)
        try:
            header, png = self.daemon.request(payload)
        except (RenderError, OSError, json.JSONDecodeError) as exc:
            self.daemon.stop()
            if self._cancelled:
                return RenderOutcome(False, error="cancelled")
            return RenderOutcome(False, error=f"WSL render failed: {exc}")
        if not header.get("ok"):
            return RenderOutcome(False, log=header.get("log", ""),
                                 error=header.get("error", "render failed"),
                                 elapsed_ms=float(header.get("ms") or 0))
        return RenderOutcome(True, png, log=header.get("log", ""),
                             elapsed_ms=float(header.get("ms") or 0))

    def _render_gui(self, text: str, quality: Quality) -> RenderOutcome:
        """Drive the Windows GUI engine in batch mode.

        Normal quality uses ``/RENDER scene /EXIT``; the Avogadro-style
        ``quality.full`` mode uses ``+I/+O`` plus its complete switch set and
        keeps the process alive (``+D``/``+P``), so completion is detected from
        the output file becoming size-stable.
        """
        import tempfile

        if not self.gui:
            return RenderOutcome(False, error="no GUI engine")
        with tempfile.TemporaryDirectory(prefix="molpov_") as tmp:
            scene = os.path.join(tmp, "scene.pov")
            out = os.path.join(tmp, "out.png")
            with open(scene, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            full = quality.full
            if full:
                args = ([self.gui] + quality.cli_args(display=True)
                        + ["+I" + scene, "+O" + out])
            else:
                args = ([self.gui, "/RENDER", scene, "/EXIT"]
                        + quality.cli_args() + ["+O" + out])
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE: no window flash
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._gui_proc = proc
            deadline = time.time() + 300
            last_size = -1
            stable_since = None
            try:
                while proc.poll() is None and time.time() < deadline:
                    _dismiss_pov_dialogs(proc.pid)
                    if full:
                        _hide_pov_windows(proc.pid)
                        if os.path.exists(out):
                            size = os.path.getsize(out)
                            if size > 0:
                                if size == last_size:
                                    if stable_since is None:
                                        stable_since = time.time()
                                    elif time.time() - stable_since > 0.6:
                                        with open(out, "rb") as probe:
                                            probe.seek(max(0, size - 8))
                                            if probe.read().endswith(
                                                    b"IEND\xaeB`\x82"):
                                                break
                                else:
                                    stable_since = None
                                    last_size = size
                        time.sleep(0.1)
                        continue
                    time.sleep(0.08)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(5)
                self._gui_proc = None
            if not os.path.exists(out):
                if self._cancelled:
                    return RenderOutcome(False, error="cancelled")
                return RenderOutcome(False, error="GUI engine produced no output")
            with open(out, "rb") as handle:
                data = handle.read()
            if not data.startswith(b"\x89PNG"):
                return RenderOutcome(False, error="GUI engine wrote an invalid PNG")
            return RenderOutcome(True, data)

    # ------------------------------------------------------------ lifecycle
    def cancel(self) -> None:
        """Abort the render currently in flight (called from the GUI thread)."""
        self._cancelled = True
        if self._gui_proc is not None and self._gui_proc.poll() is None:
            try:
                self._gui_proc.kill()
            except OSError:
                pass
            self._gui_proc = None
        if self._native_proc is not None and self._native_proc.poll() is None:
            try:
                self._native_proc.kill()
            except OSError:
                pass
            self._native_proc = None
        if self.daemon is not None:
            self.daemon.stop()

    def close(self) -> None:
        self.cancel()
