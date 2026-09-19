"""Start/stop only this project's isolated, preinstalled Open WebUI preview."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "open-webui-preview"
PROCESS_FILE = STATE / "processes.json"
VENDOR_PYTHON = Path("/opt/anaconda3/bin/python")
GATE = ROOT / "src" / "enterprise_pdf_rag" / "adapters" / "http" / "webui_gate.py"


def start(profile: str = "aia-source-review") -> None:
    if PROCESS_FILE.exists():
        raise SystemExit(
            "Preview process record already exists; run the stop command first."
        )
    if not VENDOR_PYTHON.is_file():
        raise SystemExit(
            "The explicit preinstalled 0.6.5 preview runtime is unavailable. Use Compose."
        )
    for port in (8766, 8767):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    processes: dict[str, dict[str, str | int]] = {}
    commands = {
        "api": [
            str(ROOT / ".venv" / "bin" / "python"),
            "-m",
            "uvicorn",
            "enterprise_pdf_rag.adapters.http.app:create_configured_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8766",
        ],
        "webui": [
            str(VENDOR_PYTHON),
            str(GATE),
            "--preview-legacy",
            "--profile",
            profile,
            "--data-dir",
            str(STATE / "vendor"),
        ],
    }
    for name, command in commands.items():
        environment = {
            "PATH": f"{Path(command[0]).parent}:/usr/bin:/bin",
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        if name == "api":
            environment["APP_EXECUTION_MODE"] = profile
        with (STATE / f"{name}.log").open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        processes[name] = {
            "pid": process.pid,
            "marker": "enterprise_pdf_rag.adapters.http.app"
            if name == "api"
            else str(GATE.parent),
        }
    PROCESS_FILE.write_text(json.dumps(processes, indent=2) + "\n")
    PROCESS_FILE.chmod(0o600)
    print("Started local processes; readiness must be checked in the logs.")
    print(
        "Open WebUI 0.6.5 compatibility preview: http://127.0.0.1:8767\nAPI: http://127.0.0.1:8766"
    )
    print(f"Logs and PID record: {STATE}")
    print("Stop: uv run --locked python scripts/webui_preview.py stop")


def stop() -> None:
    if not PROCESS_FILE.exists():
        print("No project preview process record exists.")
        return
    processes: dict[str, dict[str, str | int]] = json.loads(PROCESS_FILE.read_text())
    live: list[int] = []
    for name, record in processes.items():
        pid = int(record["pid"])
        check = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode:
            continue
        cwd = subprocess.run(
            ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            f"n{ROOT}" not in cwd.stdout.splitlines()
            or str(record["marker"]) not in check.stdout
        ):
            raise SystemExit(
                f"PID for {name} no longer matches this project; refusing to stop it."
            )
        live.append(pid)
    for pid in live:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15.0
    while live and time.monotonic() < deadline:
        live = [pid for pid in live if _running(pid)]
        if live:
            time.sleep(0.2)
    if live:
        raise SystemExit(
            "Project processes are still shutting down; PID record retained. Retry stop shortly."
        )
    PROCESS_FILE.unlink()
    print("Stopped only recorded project preview processes. Data is retained.")


def _running(pid: int) -> bool:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip().startswith("Z")


def status() -> int:
    opener = build_opener(ProxyHandler({}))
    ready = True
    for name, url in (
        ("api", "http://127.0.0.1:8766/v1/models"),
        ("webui", "http://127.0.0.1:8767/api/config"),
    ):
        try:
            with opener.open(url, timeout=3) as response:
                code = response.status
        except (OSError, URLError):
            code = 0
        print(f"{name}: HTTP {code} — {url}")
        ready = ready and code == 200
    print(f"PID record: {PROCESS_FILE}")
    return 0 if ready else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument(
        "--profile",
        choices=("aia-source-review", "offline-demo"),
        default="aia-source-review",
    )
    args = parser.parse_args()
    if args.action == "start":
        start(args.profile)
    elif args.action == "stop":
        stop()
    else:
        return status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
