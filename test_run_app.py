import os
import socket
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LAUNCHER = ROOT / "Run App.command"
SWIFT = ROOT / "macos" / "SparkzCreatorLauncher.swift"


class ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/api/bootstrap"):
            body = b'{"formats":[]}'
        else:
            body = b"<title>Sparkz Creator</title>"
        self._send(body)

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def bash(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["RUN_APP_SOURCE_ONLY"] = "1"
    if env:
        merged.update(env)
    full = f'source "{LAUNCHER}"\n{script}'
    return subprocess.run(
        ["/bin/bash", "-c", full],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


class RunAppTests(unittest.TestCase):
    def test_reopen_adopts_a_ready_app_without_the_port_error(self) -> None:
        server, port = serve(ReadyHandler)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        completed = subprocess.run(
                ["/bin/bash", str(LAUNCHER)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "RUN_APP_NO_BROWSER": "1",
                    "SPARKZ_API_PORT": str(port),
                    "SPARKZ_WEB_PORT": str(port),
                    "SPARKZ_LOCK_DIR": tempfile.mkdtemp(prefix="sparkz-lock-"),
                },
                check=False,
            )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("Starting Sparkz Creator...", output)
        self.assertIn("Checking whether Sparkz Creator is already running...", output)
        self.assertIn("Sparkz Creator is already running", output)
        self.assertIn("Opening the existing app.", output)
        self.assertNotIn("already in use", output)
        self.assertNotIn("Could not start the app", output)
        self.assertNotIn("Checking Python dependencies", output)

    def test_missing_node_is_logged_after_the_running_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            bindir = Path(temp_name) / "bin"
            bindir.mkdir()
            curl = bindir / "curl"
            curl.symlink_to("/usr/bin/curl")
            completed = subprocess.run(
                ["/bin/bash", str(LAUNCHER)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={
                    "PATH": str(bindir),
                    "HOME": temp_name,
                    "RUN_APP_NO_BROWSER": "1",
                    "SPARKZ_API_PORT": "9",
                    "SPARKZ_WEB_PORT": "9",
                    "SPARKZ_LOCK_DIR": str(Path(temp_name) / "lock"),
                },
                check=False,
            )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 1, output)
        self.assertLess(
            output.index("No running app found."),
            output.index("Checking Node.js and curl..."),
        )
        self.assertIn("Node.js/npm is not installed.", output)
        self.assertNotIn("Stop the other app and try again.", output)

    def test_heartbeat_logs_while_a_check_is_still_running(self) -> None:
        completed = bash(
            textwrap.dedent(
                """
                HEARTBEAT_SECONDS=1
                run_with_heartbeat "Checking Python dependencies..." sleep 1.6
                """
            )
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("Checking Python dependencies...", output)
        self.assertIn("Still working: Checking Python dependencies...", output)

    def test_foreign_listener_is_left_alone(self) -> None:
        port = free_port()
        other = free_port()
        process = subprocess.Popen(
            ["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd="/tmp",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(reap, process)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        completed = bash(
            textwrap.dedent(
                f"""
                API_PORT={port}
                WEB_PORT={other}
                API_URL="http://127.0.0.1:$API_PORT"
                APP_URL="http://127.0.0.1:$WEB_PORT"
                echo "plan=$(startup_plan)"
                echo "api=$(port_owner_kind "$API_PORT")"
                stop_our_listeners
                echo "after=$(port_owner_kind "$API_PORT")"
                """
            )
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("plan=busy", output)
        self.assertIn("api=foreign", output)
        self.assertIn("after=foreign", output)
        self.assertIsNone(process.poll())

    def test_our_stuck_listener_is_replaced(self) -> None:
        port = free_port()
        other = free_port()
        process = subprocess.Popen(
            ["python3", "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(reap, process)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        completed = bash(
            textwrap.dedent(
                f"""
                API_PORT={port}
                WEB_PORT={other}
                API_URL="http://127.0.0.1:$API_PORT"
                APP_URL="http://127.0.0.1:$WEB_PORT"
                echo "plan=$(startup_plan)"
                echo "api=$(port_owner_kind "$API_PORT")"
                stop_our_listeners
                echo "after=$(port_owner_kind "$API_PORT")"
                """
            )
        )
        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("plan=replace", output)
        self.assertIn("api=ours", output)
        self.assertIn("after=free", output)
        process.wait(timeout=5)
        self.assertNotEqual(process.returncode, None)

    def test_dependency_check_is_logged_before_it_runs(self) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        check = text.index('run_with_heartbeat "Checking Python dependencies..."')
        imported = text.index("import fastapi, uvicorn, yt_dlp")
        self.assertLess(check, imported)
        self.assertIn("Still working:", text)
        self.assertNotIn("Stop the other app and try again.", text)

    def test_launcher_waits_on_log_progress_instead_of_twenty_seconds(self) -> None:
        source = SWIFT.read_text(encoding="utf-8")
        self.assertNotIn("attempt < 80", source)
        self.assertIn("stallLimit: TimeInterval = 60", source)
        self.assertIn("maxStartup: TimeInterval = 10 * 60", source)
        self.assertIn("noteLogProgress", source)
        self.assertIn("ownsServers = false", source)


if __name__ == "__main__":
    unittest.main()
