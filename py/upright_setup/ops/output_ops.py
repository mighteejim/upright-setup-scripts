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

from ..models import Config

class OutputOps:
    def emit_output(self, status: str, message: str) -> None:
        if not self.cfg.output_json:
            return
        output_path = Path(self.cfg.output_json).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        state = None
        phase = ""
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                phase = str(state.get("phase") or "")
            except Exception:
                state = None
        payload = {
            "generated_at": self.now_iso(),
            "status": status,
            "message": message,
            "phase": phase,
            "mode": {
                "dry_run": self.cfg.dry_run,
                "resume": self.cfg.resume,
                "destroy": self.cfg.destroy,
                "non_interactive": self.cfg.non_interactive,
                "auto_approve": self.cfg.auto_approve,
            },
            "config": {
                "root_domain": self.cfg.root_domain,
                "upright_suffix": self.cfg.upright_suffix,
                "dns_mode": self.cfg.dns_mode,
                "instance_type": self.cfg.instance_type,
                "provision_mode": self.cfg.provision_mode,
                "image": self.cfg.image,
                "deploy_user": self.cfg.deploy_user,
                "ssh_port": self.cfg.ssh_port,
                "ssh_pubkey_path": self.cfg.ssh_pubkey_path,
                "app_region": self.cfg.app_region,
                "ord_region": self.cfg.ord_region,
                "iad_region": self.cfg.iad_region,
                "sea_region": self.cfg.sea_region,
                "stackscript_id": self.cfg.stackscript_id,
                "local_repo_path": self.cfg.local_repo_path,
                "local_repo_url": self.cfg.local_repo_url,
                "local_ruby_version": self.cfg.local_ruby_version,
                "bootstrap_local_app": self.cfg.bootstrap_local_app,
            },
            "state_path": str(self.state_path.relative_to(self.cwd)),
            "state": state,
        }
        output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
