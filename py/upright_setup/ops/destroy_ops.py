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

class DestroyOps:
    def confirm_destroy_flow(self) -> None:
        if self.cfg.dry_run:
            self.info("DRY-RUN: destroy confirmation skipped")
            return
        if self.cfg.confirm_destroy:
            self.info("Destroy confirmed via --confirm-destroy")
            return
        if self.cfg.non_interactive:
            self.die("--non-interactive destroy requires --confirm-destroy")
        print(f"\nThis will delete Linodes and DNS records tracked in {self.state_path.relative_to(self.cwd)}.")
        if input("Type DESTROY to continue: ").strip() != "DESTROY":
            self.die("Destroy cancelled")

    def delete_dns_for_mode(self) -> None:
        if self.cfg.dns_mode == "linode-dns":
            self.delete_linode_dns_records()
        elif self.cfg.dns_mode == "cloudflare-dns":
            self.delete_cloudflare_dns_records()
        elif self.cfg.dns_mode == "manual":
            self.info("Manual DNS mode; skipping DNS record deletion (use --dns-mode linode-dns/cloudflare-dns to override)")
        else:
            self.warn(f"Unknown DNS mode {self.cfg.dns_mode}; skipping DNS deletion")

    def delete_linode_dns_records(self) -> None:
        domains = self.linode_json("domains", "list", "--domain", self.cfg.root_domain)
        row = domains[0] if isinstance(domains, list) and domains else None
        domain_id = str((row or {}).get("id") or "")
        if not domain_id:
            self.warn(f"No Linode DNS zone found for {self.cfg.root_domain}; skipping")
            return
        for code in ["app", "ord", "iad", "sea"]:
            tracked = self.dns_record(code)
            if tracked.get("provider") != "linode-dns":
                self.info(f"No tracked Linode DNS record for {code}; skipping")
                continue
            rid = tracked.get("record_id", "")
            if not rid:
                self.info(f"Tracked Linode DNS record id missing for {code}; skipping")
                continue
            name = tracked.get("name") or self.record_name_for_code(code)
            if self.cfg.dry_run:
                self.info(f"DRY-RUN: would delete tracked Linode DNS record id={rid} name={name}")
                continue
            self.info(f"Deleting tracked Linode DNS record id={rid} name={name}")
            self.linode_cmd("domains", "records-delete", domain_id, rid)

    def delete_cloudflare_dns_records(self) -> None:
        if not self.cfg.dry_run:
            if not self.cfg.cloudflare_api_token:
                if self.cfg.non_interactive:
                    self.die("--non-interactive with cloudflare-dns requires --cloudflare-token")
                import getpass

                self.cfg.cloudflare_api_token = getpass.getpass("Cloudflare API token (Zone DNS Edit + Zone Read): ").strip()
            self.resolve_cloudflare_zone()
        for code in ["app", "ord", "iad", "sea"]:
            tracked = self.dns_record(code)
            if tracked.get("provider") != "cloudflare-dns":
                self.info(f"No tracked Cloudflare DNS record for {code}; skipping")
                continue
            rid = tracked.get("record_id", "")
            fqdn = tracked.get("fqdn") or self.fqdn(code)
            if not rid:
                self.info(f"Tracked Cloudflare DNS record id missing for {code}; skipping")
                continue
            if self.cfg.dry_run:
                self.info(f"DRY-RUN: would delete tracked Cloudflare DNS record id={rid} name={fqdn}")
                continue
            self.info(f"Deleting tracked Cloudflare DNS record id={rid} name={fqdn}")
            self.cloudflare_api("DELETE", f"/zones/{self.cfg.cloudflare_zone_id}/dns_records/{rid}")

    def delete_linodes(self) -> None:
        for code in ["app", "ord", "iad", "sea"]:
            linode_id = self.node_linode_id(code)
            if not linode_id or linode_id == "null":
                linode_id = self.lookup_linode_id_by_label(code)
            if not linode_id or linode_id == "null":
                self.info(f"No Linode found for {code}; skipping")
                continue
            if self.cfg.dry_run:
                self.info(f"DRY-RUN: would delete Linode id={linode_id} ({code})")
            else:
                self.info(f"Deleting Linode id={linode_id} ({code})")
                proc = subprocess.run(["linode-cli", "linodes", "delete", linode_id], cwd=self.cwd, env={**os.environ, **(self.linode_env() or {})})
                if proc.returncode != 0:
                    self.warn(f"Failed deleting Linode id={linode_id}")

    def delete_local_scaffold_repo(self) -> None:
        assert self.state is not None
        cfg = self.state.get("config", {}) if isinstance(self.state, dict) else {}
        created = bool((cfg or {}).get("local_repo_created_by_wizard", False))
        repo_path_raw = str((cfg or {}).get("local_repo_path") or "").strip()
        if not created:
            inferred = self.inferred_local_repo_dir()
            if (
                inferred.exists()
                and inferred.is_dir()
                and inferred != self.cwd
                and self.is_rails_app(inferred)
                and not self.repo_has_git_commits(inferred)
            ):
                repo_path_raw = str(inferred)
                self.warn(
                    "State does not mark local scaffold as wizard-created; "
                    f"using inferred scratch repo fallback: {repo_path_raw}"
                )
            else:
                self.info("Local app repo was not marked as wizard-created; skipping local scaffold deletion")
                return
        if not repo_path_raw:
            self.warn("State indicates wizard-created local app repo, but local_repo_path is missing; skipping")
            return
        repo_path = Path(repo_path_raw).expanduser()
        if not repo_path.is_absolute():
            repo_path = (self.cwd / repo_path).resolve()
        else:
            repo_path = repo_path.resolve()
        if repo_path == self.cwd:
            self.warn(f"Refusing to delete repo root as local scaffold path: {repo_path}")
            return
        if not repo_path.exists():
            self.info(f"Local scaffold path not found; skipping: {repo_path}")
            return
        if not repo_path.is_dir():
            self.warn(f"Local scaffold path is not a directory; skipping: {repo_path}")
            return
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would delete wizard-created local app scaffold at {repo_path}")
            return
        self.info(f"Deleting wizard-created local app scaffold at {repo_path}")
        shutil.rmtree(repo_path)

    def expected_linode_label(self, code: str) -> str:
        root = (self.cfg.root_domain or "").strip()
        if not root:
            return ""
        return f"upright-{code}-{root.replace('.', '-')}"

    def lookup_linode_id_by_label(self, code: str) -> str:
        label = self.expected_linode_label(code)
        if not label:
            self.info(f"No Linode id for {code}; no root domain available for label lookup")
            return ""
        self.info(f"No Linode id for {code}; trying label lookup ({label})")
        rows = self.linode_json("linodes", "list", "--label", label)
        if not isinstance(rows, list) or not rows:
            return ""
        exact = [r for r in rows if str(r.get("label") or "") == label]
        matches = exact or rows
        if len(matches) > 1:
            self.warn(f"Multiple Linodes matched label {label}; using first id")
        resolved = str((matches[0] or {}).get("id") or "")
        if resolved:
            self.info(f"Resolved Linode {code} by label {label}: id={resolved}")
        return resolved

    def maybe_delete_stackscript_flow(self) -> None:
        sid = self.cfg.stackscript_id
        if not sid or sid == "null":
            return
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would optionally delete StackScript id={sid}")
            return
        if self.cfg.delete_stackscript:
            self.linode_cmd("stackscripts", "delete", sid)
            return
        if self.cfg.non_interactive:
            self.info("Skipping StackScript deletion (set --delete-stackscript to enable)")
            return
        yn = input(f"Delete private StackScript id={sid} too? [y/N]: ").strip()
        if re.match(r"^[Yy]$", yn):
            self.linode_cmd("stackscripts", "delete", sid)

    def archive_state(self) -> None:
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived = self.cwd / f"infra/state.destroyed.{stamp}.json"
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would archive {self.state_path.relative_to(self.cwd)} to {archived.relative_to(self.cwd)}")
            if self.passwords_path.exists():
                pass_archived = self.cwd / f"infra/passwords.destroyed.{stamp}.json"
                self.info(
                    f"DRY-RUN: would archive {self.passwords_path.relative_to(self.cwd)} to "
                    f"{pass_archived.relative_to(self.cwd)}"
                )
            return
        self.state_path.rename(archived)
        self.info(f"Archived state to {archived.relative_to(self.cwd)}")
        if self.passwords_path.exists():
            pass_archived = self.cwd / f"infra/passwords.destroyed.{stamp}.json"
            self.passwords_path.rename(pass_archived)
            self.info(f"Archived passwords to {pass_archived.relative_to(self.cwd)}")
