from __future__ import annotations

import json
import os
import random
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

class ProvisionOps:
    def ensure_stackscript(self) -> None:
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would create/update private StackScript '{self.cfg.stackscript_label}'")
            self.cfg.stackscript_id = "0"
            return
        script = self.stackscript_path.read_text(encoding="utf-8")
        existing = self.linode_json("stackscripts", "list", "--mine", "true", "--label", self.cfg.stackscript_label)
        first = existing[0] if isinstance(existing, list) and existing else None
        stack_id = str((first or {}).get("id") or "")
        if stack_id:
            self.info(f"Updating existing StackScript id={stack_id}")
            self.linode_cmd(
                "stackscripts",
                "update",
                stack_id,
                "--label",
                self.cfg.stackscript_label,
                "--images",
                self.cfg.image,
                "--is_public",
                "false",
                "--script",
                script,
                "--rev_note",
                f"upright-bootstrap sync {self.now_iso()}",
            )
        else:
            self.info(f"Creating private StackScript '{self.cfg.stackscript_label}'")
            created = self.linode_cmd(
                "stackscripts",
                "create",
                "--label",
                self.cfg.stackscript_label,
                "--description",
                "Bootstrap Ubuntu host for Upright deployment",
                "--images",
                self.cfg.image,
                "--is_public",
                "false",
                "--script",
                script,
                json_out=True,
            )
            row = created[0] if isinstance(created, list) and created else (created or {})
            stack_id = str(row.get("id") or "")
        if not stack_id:
            self.die("Failed to resolve StackScript ID")
        self.cfg.stackscript_id = stack_id
        self.info(f"Using StackScript id={stack_id}")

    def _linode_status(self, linode_id: str) -> tuple[str, str]:
        payload = self.linode_json("linodes", "view", str(linode_id))
        row = payload[0] if isinstance(payload, list) and payload else (payload or {})
        status = str(row.get("status") or "unknown")
        ips = row.get("ipv4") or []
        ipv4 = str(ips[0]) if isinstance(ips, list) and ips else ""
        return status, ipv4

    def create_node(self, code: str, region: str, fqdn: str, role: str) -> str:
        assert self.state is not None
        for node in self.state.get("nodes", []):
            if node.get("code") == code and node.get("linode_id"):
                existing = str(node["linode_id"])
                self.info(f"Node {code} already has linode_id={existing}; skipping create")
                return existing
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would create node code={code} region={region} fqdn={fqdn}")
            return ""
        label = f"upright-{code}-{self.cfg.root_domain.replace('.', '-') }"
        root_password = secrets.token_urlsafe(24)
        deploy_password = secrets.token_urlsafe(24)
        stackscript_data = json.dumps(
            {
                "DEPLOY_USER": self.cfg.deploy_user,
                "DEPLOY_PASSWORD": deploy_password,
                "DEPLOY_SSH_PUBKEY": self.cfg.ssh_pubkey_value,
                "SSH_PORT": self.cfg.ssh_port,
                "NODE_ROLE": role,
                "NODE_FQDN": fqdn,
                "ENABLE_FAIL2BAN": "yes",
            }
        )
        interfaces = '[{"public":{},"primary":true,"firewall_id":null}]'
        self.info(f"Creating Linode {label} in {region}")
        created = self.linode_cmd(
            "linodes",
            "create",
            "--label",
            label,
            "--region",
            region,
            "--type",
            self.cfg.instance_type,
            "--image",
            self.cfg.image,
            "--root_pass",
            root_password,
            "--stackscript_id",
            self.cfg.stackscript_id,
            "--stackscript_data",
            stackscript_data,
            "--interface_generation",
            "linode",
            "--interfaces",
            interfaces,
            json_out=True,
        )
        row = created[0] if isinstance(created, list) and created else (created or {})
        linode_id = str(row.get("id") or "")
        if not linode_id:
            self.die(f"Failed to parse Linode ID for {code}")
        self.update_node(code, linode_id=int(linode_id), status="provisioning")
        self.record_node_passwords(
            code,
            linode_id=linode_id,
            fqdn=fqdn,
            root_password=root_password,
            deploy_password=deploy_password,
        )
        return linode_id

    def _wait_for_linode_batch(self, created: dict[str, dict[str, str]]) -> None:
        def status_style(status: str) -> str:
            s = (status or "").lower()
            if s == "running":
                return "32;1"
            if s in {"booting", "provisioning", "rebooting", "rebuilding", "starting"}:
                return "33;1"
            if s in {"offline", "stopped", "error", "failed", "deleting"}:
                return "31;1"
            return "36;1"

        pending = set(created.keys())
        faces = [
            "<(^_^)<",
            ">(^_^)>",
            "(^_^)",
            "(^o^)",
            "(o_o)",
            "(>_<)",
            "(._.)",
            "(^.^)",
            "(^_~)",
            "\\(^_^)/",
            "\\(o_o)/",
            "<(^_^<)",
            "(>^_^)>",
        ]
        poll_interval = 5.0
        frame_interval = 0.25
        max_wait_seconds = 40 * poll_interval
        start = time.monotonic()
        next_poll_at = start
        face_idx = 0
        face = faces[face_idx]
        next_face_change_at = start + random.uniform(0.35, 1.10)

        while True:
            now = time.monotonic()
            did_poll = False
            if now >= next_poll_at:
                did_poll = True
                for code in list(pending):
                    linode_id = created[code]["linode_id"]
                    status, ipv4 = self._linode_status(linode_id)
                    created[code]["status"] = status
                    created[code]["ipv4"] = ipv4
                    # DNS can be configured as soon as a public IPv4 is assigned.
                    if ipv4:
                        pending.remove(code)
                next_poll_at = now + poll_interval

            plain_states = " ".join(f"{code}:{created[code]['status']}" for code in ["app", "ord", "iad", "sea"] if code in created)
            if now >= next_face_change_at:
                face_idx = (face_idx + 1) % len(faces)
                face = faces[face_idx]
                next_face_change_at = now + random.uniform(0.35, 1.10)
            if self._stdout_tty:
                colored_states = " ".join(
                    f"{code}:{self._style(created[code]['status'], status_style(created[code]['status']))}"
                    for code in ["app", "ord", "iad", "sea"]
                    if code in created
                )
                spinner = self._style(f"<< {face} >>", "36;1")
                line = f"Waiting for Linode IPv4: {colored_states} {spinner}"
                self.render_bottom_status(line)
            elif did_poll:
                self.info(f"Waiting for Linode IPv4: {plain_states}")

            if not pending:
                break
            if now - start >= max_wait_seconds:
                break

            time.sleep(frame_interval)

        if self._stdout_tty:
            self.clear_bottom_status()
        if pending:
            self.warn(f"Timed out waiting for Linode IPv4: {', '.join(sorted(pending))}")

        for code in ["app", "ord", "iad", "sea"]:
            if code not in created:
                continue
            row = created[code]
            self.update_node(
                code,
                linode_id=int(row["linode_id"]),
                ipv4=row.get("ipv4", ""),
                status=row.get("status", "unknown"),
            )

    def provision_nodes(self) -> None:
        self.info("Provisioning Linodes")
        self.set_phase("provisioning")

        if self.cfg.dry_run:
            self.create_node("app", self.cfg.app_region, self.fqdn("app"), "app")
            self.create_node("ord", self.cfg.ord_region, self.fqdn("ord"), "ord")
            self.create_node("iad", self.cfg.iad_region, self.fqdn("iad"), "iad")
            self.create_node("sea", self.cfg.sea_region, self.fqdn("sea"), "sea")
            self.set_phase("planned")
            return

        created: dict[str, dict[str, str]] = {}
        for code, region, role in [
            ("app", self.cfg.app_region, "app"),
            ("ord", self.cfg.ord_region, "ord"),
            ("iad", self.cfg.iad_region, "iad"),
            ("sea", self.cfg.sea_region, "sea"),
        ]:
            linode_id = self.create_node(code, region, self.fqdn(code), role)
            if linode_id:
                created[code] = {
                    "linode_id": linode_id,
                    "region": region,
                    "fqdn": self.fqdn(code),
                    "status": "provisioning",
                    "ipv4": "",
                }

        if created:
            rows = [[code, row["linode_id"], row["region"], row["fqdn"]] for code, row in created.items()]
            print("\nLinode create requests:")
            print(self.render_table(["code", "linode_id", "region", "fqdn"], rows))

            self._wait_for_linode_batch(created)

            final_rows = [
                [code, row["linode_id"], row.get("status", "unknown"), row.get("ipv4", "") or "-"]
                for code, row in created.items()
            ]
            print("\nLinode provisioning results:")
            print(self.render_table(["code", "linode_id", "status", "ipv4"], final_rows))

        self.set_phase("planned" if self.cfg.dry_run else "provisioned")
