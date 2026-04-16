"""Browser-based visual companion for discussions."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

console = Console()


class VisualCompanion:
    """Manages the Node.js server that serves HTML screens to a browser."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.visual_dir = self.base_dir / "visual"
        self.session_dir = self.base_dir / ".visual" / f"session-{int(time.time())}"
        self.content_dir = self.session_dir / "content"
        self.state_dir = self.session_dir / "state"
        self.process: Optional[subprocess.Popen] = None
        self.url: Optional[str] = None
        self._screen_counter = 0

    def start(self) -> Optional[str]:
        """Start the server and return the URL."""
        server_script = self.visual_dir / "server.cjs"
        if not server_script.exists():
            console.print(f"[red]Visual server not found: {server_script}[/red]")
            return None

        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["BRAINSTORM_DIR"] = str(self.session_dir)
        env["BRAINSTORM_HOST"] = "127.0.0.1"
        env["BRAINSTORM_URL_HOST"] = "localhost"

        try:
            self.process = subprocess.Popen(
                ["node", str(server_script)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(self.visual_dir),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            console.print(f"[red]Failed to start visual server: {e}[/red]")
            return None

        info_file = self.state_dir / "server-info"
        for _ in range(50):
            if info_file.exists():
                try:
                    data = json.loads(info_file.read_text(encoding="utf-8").strip())
                    self.url = data.get("url")
                    return self.url
                except Exception:
                    pass
            time.sleep(0.1)

        console.print("[yellow]Visual server started but connection info not found[/yellow]")
        return None

    def write_screen(self, content: str, name: str = "") -> str:
        """Write an HTML screen. Returns the filename used."""
        self._screen_counter += 1
        filename = (
            f"{self._screen_counter:03d}-{name}.html"
            if name
            else f"{self._screen_counter:03d}-screen.html"
        )
        screen_path = self.content_dir / filename
        screen_path.write_text(content, encoding="utf-8")
        return filename

    def write_waiting_screen(self) -> str:
        """Push a waiting screen."""
        return self.write_screen(
            '<div style="display:flex;align-items:center;justify-content:center;min-height:60vh">'
            '<p class="subtitle">Waiting for next visual update...</p></div>',
            "waiting",
        )

    def read_events(self) -> List[Dict]:
        """Read user interaction events from the browser."""
        events_file = self.state_dir / "events"
        if not events_file.exists():
            return []
        events: List[Dict] = []
        for line in events_file.read_text(encoding="utf-8").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass
        return events

    def stop(self):
        """Stop the server process."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
