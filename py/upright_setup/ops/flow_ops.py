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

class FlowOps:
    def resume_mode(self) -> None:
        self.load_state()
        self.ensure_linode_auth()
        phase = self.cfg.current_phase
        if phase in {"planning", "planned", "provisioning"}:
            self.load_ssh_pubkey()
            self.ensure_stackscript()
            if self.state is not None:
                self.state.setdefault("stackscript", {})["id"] = int(self.cfg.stackscript_id or 0)
                self.save_state()
            self.provision_nodes()
            self.configure_dns()
            self.write_config_files()
            self.run_kamal_deploy()
            self.post_deploy_checks()
        elif phase in {"provisioned", "dns_configuring"}:
            self.configure_dns()
            self.write_config_files()
            self.run_kamal_deploy()
            self.post_deploy_checks()
        elif phase in {"dns_configured", "config_generating", "config_generated"}:
            self.write_config_files()
            self.run_kamal_deploy()
            self.post_deploy_checks()
        elif phase == "deploying":
            self.cfg.run_deploy = "yes"
            self.run_kamal_deploy()
            self.post_deploy_checks()
        elif phase == "deployed":
            self.post_deploy_checks()
        else:
            self.warn(f"Unrecognized phase '{phase}'. Showing state only.")
            print(json.dumps(self.state, indent=2))
        self.info("Resume complete")

    def destroy_mode(self) -> None:
        self.load_state()
        self.ensure_linode_auth()
        self.confirm_destroy_flow()
        self.delete_dns_for_mode()
        self.delete_linodes()
        self.delete_local_scaffold_repo()
        self.maybe_delete_stackscript_flow()
        self.archive_state()
        self.info("Destroy flow complete")

    def run_main(self) -> None:
        self.banner()
        self.preflight()

        if self.cfg.resume:
            self.resume_mode()
            self.emit_output("ok", "Resume flow complete")
            return
        if self.cfg.destroy:
            self.destroy_mode()
            self.emit_output("ok", "Destroy flow complete")
            return

        self.ensure_linode_auth()
        self.load_ssh_pubkey()
        self.prompt_inputs()
        self.confirm_plan()

        self.init_state()
        self.ensure_stackscript()
        if self.state is not None:
            self.state.setdefault("stackscript", {})["id"] = int(self.cfg.stackscript_id or 0)
            self.save_state()
        self.provision_nodes()
        self.configure_dns()
        self.write_config_files()
        self.run_kamal_deploy()
        self.post_deploy_checks()
        self.show_next_steps()
        if self.cfg.dry_run:
            self.info("DRY-RUN complete (infra/state.json unchanged)")
        else:
            self.info(f"Wrote {self.state_path.relative_to(self.cwd)}")
        self.emit_output("ok", "Setup flow complete")
