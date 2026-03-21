"""
Arb Scanner — Service Launcher
================================
Starts the FastAPI backend and Vite frontend, checks external API connectivity,
and keeps both processes alive until Ctrl+C.

Run via:  backend\\.venv\\Scripts\\python.exe start.py
Or:       double-click start.bat
"""
from __future__ import annotations

import base64
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent.resolve()
BACKEND_DIR  = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
VENV_PYTHON  = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
VENV_UVICORN = BACKEND_DIR / ".venv" / "Scripts" / "uvicorn.exe"

# ── Ports & URLs ───────────────────────────────────────────────────────────────
BACKEND_PORT   = 8000
FRONTEND_PORT  = 5173
BACKEND_HEALTH = f"http://localhost:{BACKEND_PORT}/api/health"
FRONTEND_URL   = f"http://localhost:{FRONTEND_PORT}"

STARTUP_TIMEOUT = 40   # seconds to wait for each service to become healthy

# ── External API checklist ─────────────────────────────────────────────────────
#  (display_name, hostname, is_critical)
#  Connectivity is verified via TCP+SSL handshake (port 443) — fast and
#  reliable even when child process handles are open on Windows.
#  is_critical=True  →  ✗ on failure (sportsbook/Kalshi data will be missing)
#  is_critical=False →  ⚠ on failure (app works, that scanner is skipped)
EXTERNAL_APIS: list[tuple[str, str, bool]] = [
    ("Kalshi         ", "api.elections.kalshi.com",    True),
    ("Action Network ", "api.actionnetwork.com",       True),   # DK / FD / Caesars
    ("Polymarket      ", "gamma-api.polymarket.com",    False),
    ("PredictIt       ", "www.predictit.org",           False),
]

# Match the User-Agent used by the scanners to avoid 403s
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── ANSI colour helpers ────────────────────────────────────────────────────────

def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

def green(t: str)  -> str: return _ansi("32", t)
def red(t: str)    -> str: return _ansi("31", t)
def yellow(t: str) -> str: return _ansi("33", t)
def cyan(t: str)   -> str: return _ansi("36", t)
def bold(t: str)   -> str: return _ansi("1",  t)
def dim(t: str)    -> str: return _ansi("2",  t)

OK   = green("✓")
FAIL = red("✗")
WARN = yellow("⚠")
SPIN = yellow("⟳")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _enable_ansi_windows() -> None:
    """Enable ANSI escape codes in the Windows console (no-op on non-Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel = ctypes.windll.kernel32          # type: ignore[attr-defined]
        kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)
    except Exception:
        pass


def _is_port_open(port: int) -> bool:
    """Return True if something is listening on localhost:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pids_on_port(port: int) -> list[str]:
    """Return a list of PIDs that have localhost:port open (Windows netstat)."""
    pids: list[str] = []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        )
        for line in out.splitlines():
            if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts:
                    pid = parts[-1]
                    if pid.isdigit() and pid not in pids:
                        pids.append(pid)
    except Exception:
        pass
    return pids


def _kill_pid(pid: str) -> bool:
    try:
        subprocess.run(
            ["taskkill", "/PID", pid, "/F"],
            check=True, capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def _tail(text: str, n: int = 6) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(f"      {dim(l)}" for l in lines[-n:])


def _http_get(url: str, timeout: int = 8) -> tuple[int, str]:
    """Simple HTTP GET — used only for localhost endpoints (backend health).

    For external API connectivity checks, use _check_host_reachable() which
    avoids urllib's multi-layer machinery that can stall on Windows when child
    processes have inherited socket handles.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(4096).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)


def _check_host_reachable(host: str, port: int = 443, timeout: int = 6) -> bool:
    """TCP + SSL handshake check — faster and more reliable than urllib on Windows.

    Verifies the remote host accepts connections on the given port. Uses a
    direct socket rather than urllib to avoid stalls caused by inherited
    file handles from child subprocesses (uvicorn, npm) on Windows.
    """
    import ssl
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw_sock:
            with ctx.wrap_socket(raw_sock, server_hostname=host):
                return True  # handshake succeeded — host is reachable
    except Exception:
        return False


# ── Steps ──────────────────────────────────────────────────────────────────────

def check_environment() -> bool:
    """Verify the venv and npm exist. Returns False if fatal."""
    print(f"\n{bold('── Pre-flight Checks ──────────────────────────────────')}")
    ok = True

    # Python venv
    if VENV_PYTHON.exists():
        print(f"  {OK}  Python venv          {dim(str(VENV_PYTHON.relative_to(ROOT)))}")
    else:
        print(f"  {FAIL}  Python venv          {red('NOT FOUND')} — expected {VENV_PYTHON}")
        print(f"       {dim('Run: cd backend && python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt')}")
        ok = False

    if not VENV_UVICORN.exists():
        print(f"  {FAIL}  uvicorn.exe          {red('NOT FOUND')} — venv may be incomplete")
        print(f"       {dim('Run: backend\\.venv\\Scripts\\pip install -r backend\\requirements.txt')}")
        ok = False

    # npm
    try:
        npm_ver = subprocess.check_output(
            ["npm", "--version"], shell=True, text=True, timeout=5,
        ).strip()
        print(f"  {OK}  Node / npm           {dim('npm ' + npm_ver)}")
    except Exception:
        print(f"  {WARN}  Node / npm           {yellow('not found in PATH')} — frontend will not start")
        # Non-fatal: backend can still run

    return ok


def free_ports() -> None:
    """Kill any processes occupying ports 8000 and 5173."""
    for port, name in [(BACKEND_PORT, "backend"), (FRONTEND_PORT, "frontend")]:
        label = f"Port {port}  {dim('('+name+')'):<22}"
        if not _is_port_open(port):
            print(f"  {OK}  {label} free")
            continue

        pids = _pids_on_port(port)
        if not pids:
            print(f"  {WARN}  {label} {yellow('occupied — could not identify PID')}")
            continue

        killed: list[str] = []
        for pid in pids:
            if _kill_pid(pid):
                killed.append(pid)

        time.sleep(0.5)   # give OS time to release the port
        if killed:
            print(f"  {OK}  {label} killed PID {', '.join(killed)}")
        else:
            print(f"  {WARN}  {label} {yellow('occupied — could not kill PID ' + ', '.join(pids))}")


def start_backend() -> subprocess.Popen | None:
    print(f"\n{bold('── Starting Services ──────────────────────────────────')}")
    print(f"  {SPIN}  Backend  (FastAPI + uvicorn)  starting...", end="\r", flush=True)

    # Use a temp log file for backend stderr — avoids both pipe-buffer deadlocks
    # (stdout=PIPE without a reader fills the buffer and stalls the child) and
    # inherited-handle issues (stderr=PIPE leaves an open file handle that can
    # interfere with subsequent urllib SSL connections on Windows).
    _log = ROOT / "backend_launcher.log"
    log_fh = open(_log, "wb")

    proc = subprocess.Popen(
        [str(VENV_UVICORN), "main:app", "--host", "0.0.0.0", "--port", str(BACKEND_PORT)],
        cwd=str(BACKEND_DIR),
        stdout=log_fh,
        stderr=log_fh,
    )
    log_fh.close()   # parent closes its handle; child keeps writing

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(1)
        if proc.poll() is not None:
            print(f"  {FAIL}  Backend  process exited unexpectedly (code {proc.returncode})")
            try:
                tail = _log.read_text(encoding="utf-8", errors="replace").splitlines()
                if tail:
                    print(_tail("\n".join(tail[-10:])))
            except Exception:
                pass
            print(f"       {dim('Full log: ' + str(_log))}")
            return None

        status, body = _http_get(BACKEND_HEALTH, timeout=4)
        if status == 200:
            try:
                data = json.loads(body)
                scanners  = data.get("active_scanners", "?")
                markets   = data.get("total_markets", "?")
                opps      = data.get("opportunities_cached", "?")
                detail    = dim(f"{scanners} scanners · {markets} markets · {opps} arbs cached")
            except Exception:
                detail = ""
            print(f"  {OK}  Backend  healthy  {detail}                              ")
            return proc

    proc.terminate()
    print(f"  {FAIL}  Backend  {red('did not become healthy within')} {STARTUP_TIMEOUT}s")
    print(f"       {dim('See log: ' + str(_log))}")
    return None


def _find_npm() -> str | None:
    """Locate the npm executable without relying on shell=True."""
    import shutil
    # Try direct PATH lookup first
    npm = shutil.which("npm")
    if npm:
        return npm
    # Common Windows install locations
    for candidate in [
        r"C:\Program Files\nodejs\npm.cmd",
        r"C:\Program Files (x86)\nodejs\npm.cmd",
        os.path.expandvars(r"%APPDATA%\npm\npm.cmd"),
        os.path.expandvars(r"%ProgramFiles%\nodejs\npm.cmd"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def start_frontend() -> subprocess.Popen | None:
    print(f"  {SPIN}  Frontend (Vite dev server)    starting...", end="\r", flush=True)

    npm_exe = _find_npm()
    if npm_exe is None:
        print(f"  {FAIL}  Frontend {red('npm not found in PATH')}")
        print(f"       {dim('Install Node.js from https://nodejs.org and retry')}")
        return None

    # Use shell=True only for .cmd wrappers (which require cmd.exe to interpret).
    # Non-.cmd executables can run directly without a shell.
    use_shell = npm_exe.lower().endswith(".cmd")

    if use_shell:
        cmd = f'"{npm_exe}" run dev -- --host 0.0.0.0'
    else:
        cmd = [npm_exe, "run", "dev", "--", "--host", "0.0.0.0"]

    proc = subprocess.Popen(
        cmd,
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=use_shell,
    )

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(1)
        if proc.poll() is not None:
            print(f"  {FAIL}  Frontend process exited unexpectedly (code {proc.returncode})")
            print(f"       {dim('Tip: cd frontend && npm install, then retry')}")
            return None
        if _is_port_open(FRONTEND_PORT):
            print(f"  {OK}  Frontend ready  {dim('→ ' + FRONTEND_URL)}                              ")
            return proc

    proc.terminate()
    print(f"  {FAIL}  Frontend {red('did not start within')} {STARTUP_TIMEOUT}s")
    return None


def check_external_apis() -> int:
    """Check external API connectivity via TCP+SSL. Returns count of reachable APIs."""
    print(f"\n{bold('── External API Connections ───────────────────────────')}", flush=True)
    reachable = 0
    for name, host, is_critical in EXTERNAL_APIS:
        print(f"  {SPIN}  {name}  checking...", end="\r", flush=True)
        ok = _check_host_reachable(host, port=443, timeout=6)
        if ok:
            print(f"  {OK}  {name}  {dim(host)}                              ", flush=True)
            reachable += 1
        else:
            sym = FAIL if is_critical else WARN
            crit = "" if is_critical else dim("  (non-critical — scanner will be skipped)")
            print(f"  {sym}  {name}  {dim(host)}  {red('unreachable')}{crit}", flush=True)
    return reachable


def check_websocket() -> bool:
    """Attempt a WebSocket upgrade handshake. Returns True on success."""
    print(f"\n{bold('── WebSocket ──────────────────────────────────────────')}", flush=True)
    label = f"ws://localhost:{BACKEND_PORT}/ws/opportunities"
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /ws/opportunities HTTP/1.1\r\n"
            f"Host: localhost:{BACKEND_PORT}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        with socket.create_connection(("127.0.0.1", BACKEND_PORT), timeout=5) as sock:
            sock.settimeout(5)   # also apply timeout to recv()
            sock.sendall(request.encode())
            response = sock.recv(512).decode("utf-8", errors="replace")
        if "101" in response:
            print(f"  {OK}  {dim(label)}", flush=True)
            return True
        print(f"  {WARN}  {dim(label)}  unexpected response", flush=True)
        return False
    except Exception as exc:
        print(f"  {FAIL}  {dim(label)}  {red(str(exc))}", flush=True)
        return False


def keep_alive(procs: list[subprocess.Popen]) -> None:
    """Monitor processes. Ctrl+C triggers graceful shutdown."""
    shutdown_requested = False

    def _handler(sig, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT,  _handler)
    signal.signal(signal.SIGTERM, _handler)

    names = ["Backend", "Frontend"]
    print(f"\n  {dim('Press Ctrl+C to stop all services.')}\n")

    while not shutdown_requested:
        for proc, name in zip(procs, names):
            if proc.poll() is not None:
                print(f"\n  {WARN}  {yellow(name + ' process has stopped')} (code {proc.returncode})")
                print(f"       {dim('Re-run start.bat to restart all services.')}")
                # Remove the dead process so we don't spam
                procs.remove(proc)
                if not procs:
                    shutdown_requested = True
        time.sleep(5)

    print(f"\n{bold('── Shutting Down ──────────────────────────────────────')}")
    for proc, name in zip(procs, names):
        if proc.poll() is None:
            print(f"  {SPIN}  Stopping {name}...", end="\r", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            print(f"  {OK}  {name} stopped                   ")
    print(f"\n  {dim('All services stopped. Goodbye.')}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # Force UTF-8 stdout so box-drawing characters render correctly on Windows
    # regardless of the console code page. The .bat also runs chcp 65001 for
    # proper rendering in cmd.exe / Windows Terminal.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _enable_ansi_windows()

    # Import urllib.parse here (always available, just ensuring it's imported)
    import urllib.parse

    width = 56
    border = "╔" + "═" * width + "╗"
    middle = "║" + bold("           ARB SCANNER  —  Service Launcher           ") + "║"
    bottom = "╚" + "═" * width + "╝"
    print(f"\n{cyan(border)}")
    print(f"{cyan(middle)}")
    print(f"{cyan(bottom)}")

    # Phase 1 — environment check
    if not check_environment():
        print(f"\n  {FAIL}  {red('Environment check failed. Fix the issues above and retry.')}\n")
        sys.exit(1)

    free_ports()

    # Phase 2 — start backend
    backend_proc = start_backend()
    if backend_proc is None:
        print(f"\n  {FAIL}  {red('Backend failed to start. Cannot continue.')}\n")
        sys.exit(1)

    # Phase 3 — start frontend
    frontend_proc = start_frontend()

    # Phase 4 — external API connectivity
    reachable = check_external_apis()
    total = len(EXTERNAL_APIS)

    # Phase 5 — WebSocket
    check_websocket()

    # Phase 6 — summary banner
    api_summary = f"{reachable}/{total} external APIs reachable"
    if reachable == total:
        summary_line = f"  ALL SERVICES RUNNING  ({api_summary})"
        sym = OK
    elif reachable >= 2:
        summary_line = f"  SERVICES RUNNING  ({api_summary})"
        sym = WARN
    else:
        summary_line = f"  DEGRADED — only {api_summary}"
        sym = FAIL

    pad = width - len(summary_line)
    procs: list[subprocess.Popen] = [backend_proc]
    if frontend_proc:
        procs.append(frontend_proc)
        fe_line  = f"  Frontend  →  {FRONTEND_URL}"
        fe_pad   = width - len(fe_line)
    be_line  = f"  Backend   →  http://localhost:{BACKEND_PORT}/api/health"
    be_pad   = width - len(be_line)
    ctrl_line = "  Press Ctrl+C to stop all services"
    ctrl_pad  = width - len(ctrl_line)

    print(f"\n{cyan('╔' + '═' * width + '╗')}", flush=True)
    print(f"{cyan('║')}{bold(summary_line)}{' ' * max(pad, 0)}{cyan('║')}", flush=True)
    if frontend_proc:
        print(f"{cyan('║')}{green(fe_line)}{' ' * max(fe_pad, 0)}{cyan('║')}", flush=True)
    print(f"{cyan('║')}{dim(be_line)}{' ' * max(be_pad, 0)}{cyan('║')}", flush=True)
    print(f"{cyan('║')}{dim(ctrl_line)}{' ' * max(ctrl_pad, 0)}{cyan('║')}", flush=True)
    print(f"{cyan('╚' + '═' * width + '╝')}", flush=True)

    # Open browser
    if frontend_proc:
        time.sleep(1)
        webbrowser.open(FRONTEND_URL)

    # Phase 7 — keep alive
    keep_alive(procs)


if __name__ == "__main__":
    main()
