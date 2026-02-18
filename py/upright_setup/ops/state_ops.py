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

class StateOps:
    def load_password_state(self) -> dict[str, Any]:
        if not self.passwords_path.exists():
            return {"generated_at": self.now_iso(), "nodes": {}}
        try:
            loaded = json.loads(self.passwords_path.read_text(encoding="utf-8"))
        except Exception:
            self.warn(f"Invalid password state JSON at {self.passwords_path.relative_to(self.cwd)}; resetting")
            return {"generated_at": self.now_iso(), "nodes": {}}
        if not isinstance(loaded, dict):
            return {"generated_at": self.now_iso(), "nodes": {}}
        loaded.setdefault("nodes", {})
        return loaded

    def save_password_state(self, payload: dict[str, Any]) -> None:
        self.passwords_path.parent.mkdir(parents=True, exist_ok=True)
        self.passwords_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def clear_password_state(self) -> None:
        if self.passwords_path.exists():
            self.passwords_path.unlink()

    def record_node_passwords(
        self,
        code: str,
        *,
        linode_id: str,
        fqdn: str,
        root_password: str,
        deploy_password: str,
    ) -> None:
        payload = self.load_password_state()
        nodes = payload.setdefault("nodes", {})
        nodes[code] = {
            "linode_id": str(linode_id),
            "fqdn": fqdn,
            "root_password": root_password,
            "deploy_password": deploy_password,
            "updated_at": self.now_iso(),
        }
        payload["updated_at"] = self.now_iso()
        self.save_password_state(payload)

    def password_rows(self) -> list[dict[str, str]]:
        payload = self.load_password_state()
        nodes = payload.get("nodes") if isinstance(payload, dict) else {}
        if not isinstance(nodes, dict):
            return []
        rows: list[dict[str, str]] = []
        for code in ["app", "ord", "iad", "sea"]:
            row = nodes.get(code)
            if not isinstance(row, dict):
                continue
            root_password = str(row.get("root_password") or "")
            deploy_password = str(row.get("deploy_password") or "")
            if not root_password and not deploy_password:
                continue
            rows.append(
                {
                    "code": code,
                    "linode_id": str(row.get("linode_id") or self.node_linode_id(code)),
                    "fqdn": str(row.get("fqdn") or self.fqdn(code)),
                    "root_password": root_password,
                    "deploy_password": deploy_password,
                }
            )
        return rows

    def save_state(self) -> None:
        if self.state is None:
            return
        if self.cfg.dry_run:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")

    def init_state(self) -> None:
        if not self.cfg.dry_run:
            self.clear_password_state()
        self.state = {
            "generated_at": self.now_iso(),
            "phase": "planning",
            "stackscript": {"label": self.cfg.stackscript_label, "id": None},
            "dns": {"mode": self.cfg.dns_mode, "status": "pending", "records": {}},
            "config": {
                "root_domain": self.cfg.root_domain,
                "upright_suffix": self.cfg.upright_suffix,
                "dns_mode": self.cfg.dns_mode,
                "registry_server": self.cfg.registry_server,
                "registry_username": self.cfg.registry_username,
                "image_name": self.cfg.image_name,
                "instance_type": self.cfg.instance_type,
                "provision_mode": self.cfg.provision_mode,
                "image": self.cfg.image,
                "deploy_user": self.cfg.deploy_user,
                "ssh_port": self.cfg.ssh_port,
                "local_repo_path": self.cfg.local_repo_path,
                "local_repo_created_by_wizard": bool(self.cfg.local_repo_created_by_wizard),
            },
            "regions": {
                "app": self.cfg.app_region,
                "ord": self.cfg.ord_region,
                "iad": self.cfg.iad_region,
                "sea": self.cfg.sea_region,
            },
            "nodes": [
                {"role": "app", "code": "app", "region": self.cfg.app_region, "fqdn": f"app.{self.cfg.upright_suffix}", "linode_id": None, "ipv4": None, "status": "planned"},
                {"role": "monitor", "code": "ord", "region": self.cfg.ord_region, "fqdn": f"ord.{self.cfg.upright_suffix}", "linode_id": None, "ipv4": None, "status": "planned"},
                {"role": "monitor", "code": "iad", "region": self.cfg.iad_region, "fqdn": f"iad.{self.cfg.upright_suffix}", "linode_id": None, "ipv4": None, "status": "planned"},
                {"role": "monitor", "code": "sea", "region": self.cfg.sea_region, "fqdn": f"sea.{self.cfg.upright_suffix}", "linode_id": None, "ipv4": None, "status": "planned"},
            ],
        }
        if self.cfg.dry_run:
            self.info("DRY-RUN: initialized planning state in memory (no infra/state.json write)")
        else:
            self.save_state()
            self.info(f"Wrote planning state to {self.state_path.relative_to(self.cwd)}")

    def load_state(self) -> None:
        if not self.state_path.exists():
            self.die(f"No state file found at {self.state_path.relative_to(self.cwd)}")
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        cfg = self.state.get("config", {})
        regions = self.state.get("regions", {})
        self.cfg.current_phase = self.state.get("phase", "planning")
        self.cfg.root_domain = cfg.get("root_domain", self.cfg.root_domain)
        self.cfg.upright_suffix = cfg.get("upright_suffix", self.cfg.upright_suffix)
        state_dns_mode = cfg.get("dns_mode", self.state.get("dns", {}).get("mode", "manual"))
        if not self.cfg.dns_mode:
            self.cfg.dns_mode = state_dns_mode
        elif self.cfg.dns_mode != state_dns_mode:
            self.info(f"Using CLI dns mode override: {self.cfg.dns_mode} (state={state_dns_mode})")
        self.cfg.registry_server = cfg.get("registry_server", self.cfg.registry_server)
        self.cfg.registry_username = cfg.get("registry_username", self.cfg.registry_username)
        self.cfg.image_name = cfg.get("image_name", self.cfg.image_name)
        self.cfg.instance_type = cfg.get("instance_type", self.cfg.instance_type)
        self.cfg.provision_mode = "classic" if cfg.get("provision_mode", "classic") == "tui" else cfg.get("provision_mode", "classic")
        self.cfg.image = cfg.get("image", self.cfg.image)
        self.cfg.deploy_user = cfg.get("deploy_user", self.cfg.deploy_user)
        self.cfg.ssh_port = cfg.get("ssh_port", self.cfg.ssh_port)
        self.cfg.local_repo_path = cfg.get("local_repo_path", self.cfg.local_repo_path)
        self.cfg.local_repo_created_by_wizard = bool(
            cfg.get("local_repo_created_by_wizard", self.cfg.local_repo_created_by_wizard)
        )
        self.cfg.app_region = regions.get("app", "")
        self.cfg.ord_region = regions.get("ord", "")
        self.cfg.iad_region = regions.get("iad", "")
        self.cfg.sea_region = regions.get("sea", "")
        stack = self.state.get("stackscript", {})
        self.cfg.stackscript_label = stack.get("label", self.cfg.stackscript_label)
        self.cfg.stackscript_id = str(stack.get("id") or "")
        self.info(f"Loaded state phase={self.cfg.current_phase}")

    def set_phase(self, phase: str) -> None:
        assert self.state is not None
        self.state["phase"] = phase
        self.save_state()

    def set_dns_status(self, status: str) -> None:
        assert self.state is not None
        self.state.setdefault("dns", {})["status"] = status
        self.save_state()

    def set_dns_record(
        self,
        code: str,
        *,
        provider: str,
        record_id: str,
        fqdn: str,
        target: str,
        zone_id: str = "",
        name: str = "",
    ) -> None:
        assert self.state is not None
        dns = self.state.setdefault("dns", {})
        records = dns.setdefault("records", {})
        records[code] = {
            "provider": provider,
            "record_id": str(record_id),
            "fqdn": fqdn,
            "name": name,
            "target": target,
            "zone_id": str(zone_id),
            "updated_at": self.now_iso(),
        }
        self.save_state()

    def dns_record(self, code: str) -> dict[str, str]:
        assert self.state is not None
        dns = self.state.get("dns")
        if not isinstance(dns, dict):
            return {}
        records = dns.get("records")
        if not isinstance(records, dict):
            return {}
        row = records.get(code)
        if not isinstance(row, dict):
            return {}
        return {
            "provider": str(row.get("provider") or ""),
            "record_id": str(row.get("record_id") or ""),
            "fqdn": str(row.get("fqdn") or ""),
            "name": str(row.get("name") or ""),
            "target": str(row.get("target") or ""),
            "zone_id": str(row.get("zone_id") or ""),
        }

    def update_node(self, code: str, *, linode_id: int | None = None, ipv4: str | None = None, status: str | None = None) -> None:
        assert self.state is not None
        for node in self.state.get("nodes", []):
            if node.get("code") == code:
                if linode_id is not None:
                    node["linode_id"] = linode_id
                if ipv4 is not None:
                    node["ipv4"] = ipv4
                if status is not None:
                    node["status"] = status
                self.save_state()
                return

    def node_ipv4(self, code: str) -> str:
        assert self.state is not None
        for node in self.state.get("nodes", []):
            if node.get("code") == code:
                return node.get("ipv4") or ""
        return ""

    def node_linode_id(self, code: str) -> str:
        assert self.state is not None
        for node in self.state.get("nodes", []):
            if node.get("code") == code:
                val = node.get("linode_id")
                return "" if val is None else str(val)
        return ""

    def record_name_for_code(self, code: str) -> str:
        if self.cfg.upright_suffix == self.cfg.root_domain:
            return code
        suffix = self.cfg.upright_suffix.removesuffix(f".{self.cfg.root_domain}")
        if suffix == self.cfg.upright_suffix or not suffix:
            return code
        return f"{code}.{suffix}"

    def fqdn(self, code: str) -> str:
        return f"{code}.{self.cfg.upright_suffix}"
