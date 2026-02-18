#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "py"))

from upright_setup.app import SetupApp, SetupError
from upright_setup.cli import parse_config


def _style_error(label: str) -> str:
    no_color = os.environ.get("NO_COLOR") is not None
    force_color = os.environ.get("CLICOLOR_FORCE") == "1"
    stderr_tty = sys.stderr.isatty() and os.environ.get("TERM") != "dumb"
    if force_color or (stderr_tty and not no_color):
        return f"\033[31m{label}\033[0m"
    return label


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        cfg = parse_config(argv)
    except ValueError as exc:
        print(f"{_style_error('[ERROR]')} {exc}", file=sys.stderr)
        return 2

    app = SetupApp(cfg=cfg, cwd=ROOT)
    try:
        app.run_main()
        return 0
    except SetupError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
