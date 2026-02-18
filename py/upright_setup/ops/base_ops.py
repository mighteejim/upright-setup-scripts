from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Config, SetupError

class BaseOps:
    def __init__(self, cfg: Config, cwd: Path) -> None:
        self.cfg = cfg
        self.cwd = cwd
        self.state_path = cwd / "infra/state.json"
        self.passwords_path = cwd / "infra/passwords.json"
        self.stackscript_path = cwd / "scripts/stackscript/upright-bootstrap.sh"
        self.state: dict[str, Any] | None = None
        no_color = os.environ.get("NO_COLOR") is not None
        force_color = os.environ.get("CLICOLOR_FORCE") == "1"
        stdout_tty = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        stderr_tty = sys.stderr.isatty() and os.environ.get("TERM") != "dumb"
        self._stdout_tty = stdout_tty
        self._color_stdout = force_color or (stdout_tty and not no_color)
        self._color_stderr = force_color or (stderr_tty and not no_color)

    def _style(self, text: str, code: str, *, stderr: bool = False) -> str:
        enabled = self._color_stderr if stderr else self._color_stdout
        if not enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def render_bottom_status(self, line: str) -> None:
        if not self._stdout_tty:
            return
        width = shutil.get_terminal_size((120, 24)).columns
        safe = line[: max(width - 1, 1)]
        # Save cursor, jump to bottom row, draw status line, restore cursor.
        print(f"\033[s\033[999;1H{safe}\033[K\033[u", end="", flush=True)

    def clear_bottom_status(self) -> None:
        if not self._stdout_tty:
            return
        print("\033[s\033[999;1H\033[K\033[u", end="", flush=True)

    def now_iso(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def info(self, msg: str) -> None:
        print(f"{self._style('[INFO]', '32')} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{self._style('[WARN]', '33')} {msg}")

    def err(self, msg: str) -> None:
        print(f"{self._style('[ERROR]', '31', stderr=True)} {msg}", file=sys.stderr)

    def die(self, msg: str) -> None:
        self.err(msg)
        self.emit_output("error", msg)
        raise SetupError(msg)

    def banner(self) -> None:
        art = """
            .-\"\"\"\"-.
         .-'  .--.  '-.
       .'   /  _  \\    '.
      /    |  (_)  |     \\
     ;      \\     /       ;
     |  .-._ '---' _.-.   |
     ; /    '-._.-'    \\  ;
      \\      /  |  \\      /
       '.   /   |   \\   .'
         '-._   |   _.-'
              '---'
"""
        title = "  Upright Linode Setup Wizard"
        subtitle = "  Multi-site bootstrap: app + 3 monitors"

        print(self._style(art.rstrip(), "34;1"))
        print(self._style(title, "36;1"))
        print(self._style(subtitle, "2"))

    def run(
        self,
        cmd: list[str],
        *,
        capture: bool = False,
        check: bool = True,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
    ) -> str:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        proc = subprocess.run(
            cmd,
            cwd=self.cwd,
            env=merged_env,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout).strip() or f"command failed: {' '.join(cmd)}"
            self.die(stderr)
        if not capture:
            if proc.stdout:
                sys.stdout.write(proc.stdout)
            if proc.stderr:
                sys.stderr.write(proc.stderr)
            return ""
        return proc.stdout

    def shell(self, script: str, *, check: bool = True) -> None:
        proc = subprocess.run(
            ["bash", "-lc", script],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            self.die((proc.stderr or proc.stdout).strip() or "shell command failed")
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)

    def check_dependency(self, cmd: str) -> None:
        if not self.has_dependency(cmd):
            self.die(f"Missing required command: {cmd}")

    def has_dependency(self, cmd: str) -> bool:
        if "/" in cmd:
            target = self.cwd / cmd
            return target.exists() and os.access(target, os.X_OK)
        return shutil.which(cmd) is not None

    def render_table(self, headers: list[str], rows: list[list[str]]) -> str:
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))

        def fmt_row(values: list[str]) -> str:
            cells = [f" {str(v).ljust(widths[i])} " for i, v in enumerate(values)]
            return "|" + "|".join(cells) + "|"

        sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        out = [sep, fmt_row(headers), sep]
        for row in rows:
            out.append(fmt_row([str(v) for v in row]))
        out.append(sep)
        return "\n".join(out)

    def ensure_repo_root(self) -> None:
        if not self.stackscript_path.exists():
            self.die(f"Run from repo root (missing {self.stackscript_path.relative_to(self.cwd)}).")

    def install_linode_cli(self) -> None:
        if shutil.which("linode-cli"):
            return
        self.warn("linode-cli not found; attempting install")
        if shutil.which("brew"):
            self.run(["brew", "install", "linode-cli"], check=True)
            return
        if shutil.which("apt-get"):
            self.run(["sudo", "apt-get", "update"], check=True)
            self.run(["sudo", "apt-get", "install", "-y", "linode-cli"], check=True)
            return
        if shutil.which("pipx"):
            self.run(["pipx", "install", "linode-cli"], check=True)
            return
        if shutil.which("pip3"):
            self.run(["pip3", "install", "--user", "linode-cli"], check=True)
            return
        self.die("Could not install linode-cli automatically. Install it and rerun.")

    def preflight(self) -> None:
        self.ensure_repo_root()
        self.install_linode_cli()
        self.check_dependency("linode-cli")
        if self.cfg.destroy:
            return
        for dep in ["ssh", "dig", "docker", "openssl", "curl"]:
            self.check_dependency(dep)

    def linode_env(self) -> dict[str, str] | None:
        if not self.cfg.linode_pat:
            return None
        return {"LINODE_CLI_TOKEN": self.cfg.linode_pat}

    def linode_json(self, *args: str) -> Any:
        out = self.run(["linode-cli", *args, "--json"], capture=True, env=self.linode_env())
        try:
            return json.loads(out) if out.strip() else []
        except json.JSONDecodeError as exc:
            self.die(f"linode-cli returned invalid JSON: {exc}")

    def linode_cmd(self, *args: str, json_out: bool = False) -> Any:
        cmd = ["linode-cli", *args]
        if json_out:
            cmd.append("--json")
        out = self.run(cmd, capture=json_out, env=self.linode_env())
        if not json_out:
            return None
        try:
            return json.loads(out) if out.strip() else []
        except json.JSONDecodeError as exc:
            self.die(f"linode-cli returned invalid JSON: {exc}")

    def ensure_linode_auth(self) -> None:
        proc = subprocess.run(["linode-cli", "account", "view", "--json"], cwd=self.cwd, capture_output=True, text=True)
        if proc.returncode == 0:
            self.info("Using existing linode-cli authentication")
            return
        if self.cfg.linode_pat:
            check = subprocess.run(
                ["linode-cli", "account", "view", "--json"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                env={**os.environ, "LINODE_CLI_TOKEN": self.cfg.linode_pat},
            )
            if check.returncode != 0:
                self.die("Linode PAT validation failed")
            self.info("Validated Linode PAT")
            return
        if self.cfg.non_interactive:
            self.die("--non-interactive: missing Linode auth. Set --linode-pat or pre-configure linode-cli auth.")
        import getpass

        print("Linode API token required. Create one: https://cloud.linode.com/profile/tokens")
        self.cfg.linode_pat = getpass.getpass("Paste Linode PAT: ").strip()
        if not self.cfg.linode_pat:
            self.die("Linode PAT is required")
        self.ensure_linode_auth()
