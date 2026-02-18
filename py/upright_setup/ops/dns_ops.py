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

class DnsOps:
    def ensure_linode_dns_records(self) -> None:
        domains = self.linode_json("domains", "list", "--domain", self.cfg.root_domain)
        row = domains[0] if isinstance(domains, list) and domains else None
        domain_id = str((row or {}).get("id") or "")
        if not domain_id:
            self.die(f"Could not find Linode DNS zone for {self.cfg.root_domain}")
        for code in ["app", "ord", "iad", "sea"]:
            name = self.record_name_for_code(code)
            fqdn = self.fqdn(code)
            ip = self.node_ipv4(code)
            if not ip:
                self.die(f"Missing IP for node {code}; cannot create DNS record")
            records = self.linode_json("domains", "records-list", domain_id, "--type", "A", "--name", name)
            rec = records[0] if isinstance(records, list) and records else None
            rec_id = str((rec or {}).get("id") or "")
            if rec_id:
                self.info(f"Updating Linode DNS A {fqdn} -> {ip}")
                if not self.cfg.dry_run:
                    updated = self.linode_cmd(
                        "domains",
                        "records-update",
                        domain_id,
                        rec_id,
                        "--target",
                        ip,
                        "--ttl_sec",
                        "120",
                        json_out=True,
                    )
                    updated_row = updated[0] if isinstance(updated, list) and updated else (updated or {})
                    rec_id = str(updated_row.get("id") or rec_id)
            else:
                self.info(f"Creating Linode DNS A {fqdn} -> {ip}")
                if not self.cfg.dry_run:
                    created = self.linode_cmd(
                        "domains",
                        "records-create",
                        domain_id,
                        "--type",
                        "A",
                        "--name",
                        name,
                        "--target",
                        ip,
                        "--ttl_sec",
                        "120",
                        json_out=True,
                    )
                    created_row = created[0] if isinstance(created, list) and created else (created or {})
                    rec_id = str(created_row.get("id") or "")
                    if not rec_id:
                        self.die(f"Failed to resolve Linode DNS record id for {fqdn}")
            if not self.cfg.dry_run and rec_id:
                self.set_dns_record(
                    code,
                    provider="linode-dns",
                    record_id=rec_id,
                    fqdn=fqdn,
                    name=name,
                    target=ip,
                    zone_id=domain_id,
                )

    def cloudflare_api(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4{path}",
            method=method,
            data=body,
            headers={
                "Authorization": f"Bearer {self.cfg.cloudflare_api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            self.die(f"Cloudflare API failed: {text}")

    def resolve_cloudflare_zone(self) -> None:
        q = urllib.parse.urlencode({"name": self.cfg.root_domain, "status": "active"})
        payload = self.cloudflare_api("GET", f"/zones?{q}")
        if not payload.get("success"):
            self.die(f"Cloudflare zone lookup failed for {self.cfg.root_domain}")
        result = payload.get("result") or []
        zone_id = (result[0] if result else {}).get("id")
        if not zone_id:
            self.die(f"No active Cloudflare zone found for {self.cfg.root_domain}")
        self.cfg.cloudflare_zone_id = str(zone_id)

    def ensure_cloudflare_dns_records(self) -> None:
        if self.cfg.dry_run:
            self.info("DRY-RUN: would manage Cloudflare DNS records")
        else:
            if not self.cfg.cloudflare_api_token:
                if self.cfg.non_interactive:
                    self.die("--non-interactive with cloudflare-dns requires --cloudflare-token")
                import getpass

                self.cfg.cloudflare_api_token = getpass.getpass("Cloudflare API token (Zone DNS Edit + Zone Read): ").strip()
                if not self.cfg.cloudflare_api_token:
                    self.die("Cloudflare API token is required")
            self.resolve_cloudflare_zone()

        for code in ["app", "ord", "iad", "sea"]:
            fqdn = self.fqdn(code)
            ip = self.node_ipv4(code)
            if not ip:
                self.die(f"Missing IP for node {code}; cannot create DNS record")
            if self.cfg.dry_run:
                self.info(f"DRY-RUN: Cloudflare A {fqdn} -> {ip}")
                continue
            q = urllib.parse.urlencode({"type": "A", "name": fqdn})
            listing = self.cloudflare_api("GET", f"/zones/{self.cfg.cloudflare_zone_id}/dns_records?{q}")
            result = listing.get("result") or []
            record = result[0] if result else None
            payload = {"type": "A", "name": fqdn, "content": ip, "ttl": 120, "proxied": False}
            record_id = ""
            if record and record.get("id"):
                record_id = str(record.get("id") or "")
                self.info(f"Updating Cloudflare DNS A {fqdn} -> {ip}")
                updated = self.cloudflare_api("PUT", f"/zones/{self.cfg.cloudflare_zone_id}/dns_records/{record['id']}", payload)
                result = updated.get("result") if isinstance(updated, dict) else {}
                if isinstance(result, dict):
                    record_id = str(result.get("id") or record_id)
            else:
                self.info(f"Creating Cloudflare DNS A {fqdn} -> {ip}")
                created = self.cloudflare_api("POST", f"/zones/{self.cfg.cloudflare_zone_id}/dns_records", payload)
                result = created.get("result") if isinstance(created, dict) else {}
                if isinstance(result, dict):
                    record_id = str(result.get("id") or "")
            if not record_id:
                self.die(f"Failed to resolve Cloudflare DNS record id for {fqdn}")
            self.set_dns_record(
                code,
                provider="cloudflare-dns",
                record_id=record_id,
                fqdn=fqdn,
                name=self.record_name_for_code(code),
                target=ip,
                zone_id=self.cfg.cloudflare_zone_id,
            )

    def show_manual_dns_instructions(self) -> None:
        print("\nCreate these A records in your DNS provider:")
        for code in ["app", "ord", "iad", "sea"]:
            print(f"  - {self.fqdn(code)}  A  {self.node_ipv4(code)}")
        if self.cfg.dry_run:
            self.info("DRY-RUN: manual DNS step skipped")
            return
        if self.cfg.non_interactive:
            if self.cfg.manual_dns_confirmed:
                self.info("Manual DNS confirmation pre-approved via --manual-dns-confirmed")
                return
            self.die("--non-interactive manual DNS mode requires --manual-dns-confirmed")
        input("Press Enter after DNS records are created...")

    def verify_dns_records(self) -> None:
        if self.cfg.dry_run:
            self.info("DRY-RUN: DNS verification skipped")
            return
        self.info("Verifying DNS propagation")
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
        poll_interval = 10.0
        frame_interval = 0.25
        for code in ["app", "ord", "iad", "sea"]:
            fqdn = self.fqdn(code)
            expected = self.node_ipv4(code)
            ok = False
            resolved = ""
            polls = 0
            start = time.monotonic()
            next_poll_at = time.monotonic()
            face_idx = 0
            face = faces[face_idx]
            next_face_change_at = start + random.uniform(0.35, 1.10)
            while polls < 30:
                now = time.monotonic()
                did_poll = False
                if now >= next_poll_at:
                    did_poll = True
                    resolved = self.run(["dig", "+short", "A", fqdn], capture=True).replace("\n", " ").strip()
                    polls += 1
                    if expected and re.search(rf"\b{re.escape(expected)}\b", resolved):
                        ok = True
                        break
                    next_poll_at = now + poll_interval

                if now >= next_face_change_at:
                    face_idx = (face_idx + 1) % len(faces)
                    face = faces[face_idx]
                    next_face_change_at = now + random.uniform(0.35, 1.10)
                if self._stdout_tty:
                    spinner = self._style(f"<< {face} >>", "36;1")
                    shown = resolved or "pending"
                    line = f"Verifying DNS {fqdn} ({polls}/30): {shown} {spinner}"
                    self.render_bottom_status(line)
                elif did_poll:
                    self.info(f"DNS check {fqdn} ({polls}/30): {resolved or 'pending'}")

                if polls >= 30:
                    break
                time.sleep(frame_interval)

            if self._stdout_tty:
                self.clear_bottom_status()
            if not ok:
                self.die(f"DNS verification failed for {fqdn}. Expected {expected}.")
            self.info(f"DNS OK: {fqdn} -> {expected}")

    def configure_dns(self) -> None:
        self.info(f"Configuring DNS via mode={self.cfg.dns_mode}")
        self.set_phase("dns_configuring")
        self.set_dns_status("in_progress")
        if self.cfg.dns_mode == "linode-dns":
            self.ensure_linode_dns_records()
        elif self.cfg.dns_mode == "cloudflare-dns":
            self.ensure_cloudflare_dns_records()
        elif self.cfg.dns_mode == "manual":
            self.show_manual_dns_instructions()
        else:
            self.die(f"Unsupported DNS mode: {self.cfg.dns_mode}")
        self.verify_dns_records()
        self.set_dns_status("configured")
        self.set_phase("dns_configured")
