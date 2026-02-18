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

class InputOps:
    PLACEHOLDER_REGISTRY_USERNAME = "your-github-username"
    PLACEHOLDER_IMAGE_NAME = "ghcr.io/your-github-username/upright"

    def prompt_required_text(self, prompt_label: str, current: str, *, disallow: set[str] | None = None) -> str:
        blocked = disallow or set()
        while True:
            entered = input(f"{prompt_label} [{current}]: ").strip()
            value = entered or current
            if not value:
                self.warn(f"{prompt_label} is required")
                continue
            if value in blocked:
                self.warn(f"{prompt_label} cannot use placeholder default; enter a real value")
                continue
            return value

    def image_name_uses_placeholder(self, image_name: str) -> bool:
        return "/your-github-username/" in image_name

    def derived_default_image_name(self) -> str:
        server = (self.cfg.registry_server or "ghcr.io").strip().rstrip("/")
        username = (self.cfg.registry_username or "").strip()
        if not username:
            return self.cfg.image_name
        return f"{server}/{username}/upright"

    def sync_image_name_default_from_registry_username(self) -> None:
        if self.cfg.registry_username == self.PLACEHOLDER_REGISTRY_USERNAME:
            return
        if self.image_name_uses_placeholder(self.cfg.image_name):
            self.cfg.image_name = self.derived_default_image_name()

    def default_local_repo_path(self) -> str:
        image = (self.cfg.image_name or "").strip()
        tail = image.split("/")[-1] if image else "upright"
        repo_name = tail.split(":")[0] if ":" in tail else tail
        repo_name = repo_name or "upright"
        return str((self.cwd / repo_name).resolve())

    def prompt_local_repo_settings(self) -> None:
        if self.cfg.deploy_mode == "remote-pass":
            return
        if not self.cfg.local_repo_path:
            default_path = self.default_local_repo_path()
            if self.cfg.non_interactive:
                self.cfg.local_repo_path = default_path
            else:
                self.cfg.local_repo_path = input(f"Local app repo path [{default_path}]: ").strip() or default_path

        repo_path = Path(self.cfg.local_repo_path).expanduser()
        if not self.cfg.local_repo_url and not repo_path.exists() and not self.cfg.non_interactive:
            self.cfg.local_repo_url = input("Local app repo URL (optional, auto-clone if missing): ").strip()

    def load_ssh_pubkey(self) -> None:
        keys = self.linode_json("sshkeys", "list")
        key_count = len(keys)
        if not self.cfg.ssh_key_source:
            if self.cfg.ssh_key_ids:
                self.cfg.ssh_key_source = "linode-ids"
            elif self.cfg.ssh_pubkey_path:
                self.cfg.ssh_key_source = "local-path"
            elif key_count > 0:
                self.cfg.ssh_key_source = "linode-all"
            else:
                self.cfg.ssh_key_source = "local-path"

        source = self.cfg.ssh_key_source
        if source == "linode-all":
            uniq = []
            seen = set()
            for k in keys:
                val = (k.get("ssh_key") or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    uniq.append(val)
            if not uniq:
                self.die("No Linode SSH keys found to use from account")
            self.cfg.ssh_pubkey_path = "linode:sshkey:all"
            self.cfg.ssh_pubkey_value = "\n".join(uniq)
            self.info(f"Using all Linode SSH keys ({key_count})")
            return

        if source == "linode-ids":
            if not self.cfg.ssh_key_ids:
                self.die("--ssh-key-source linode-ids requires --ssh-key-ids")
            requested = [x.strip() for x in self.cfg.ssh_key_ids.replace(",", " ").split() if x.strip()]
            mapping = {str(k.get("id")): k for k in keys}
            selected: list[str] = []
            labels: list[str] = []
            missing: list[str] = []
            for rid in requested:
                row = mapping.get(rid)
                if not row or not row.get("ssh_key"):
                    missing.append(rid)
                    continue
                selected.append(row["ssh_key"].strip())
                labels.append((row.get("label") or "").strip())
            if missing:
                self.die(f"Invalid Linode SSH key id(s): {' '.join(missing)}")
            selected = list(dict.fromkeys([s for s in selected if s]))
            if not selected:
                self.die("No valid Linode SSH keys selected")
            self.cfg.ssh_pubkey_path = f"linode:sshkey:{','.join(requested)}:{','.join(labels)}"
            self.cfg.ssh_pubkey_value = "\n".join(selected)
            self.info(f"Using Linode SSH key id(s): {','.join(requested)}")
            return

        if source not in {"", "local-path"}:
            self.die(f"Invalid --ssh-key-source: {source}")

        default = Path.home() / ".ssh/id_ed25519.pub"
        if not default.exists():
            default = Path.home() / ".ssh/id_rsa.pub"
        if not self.cfg.ssh_pubkey_path:
            if self.cfg.non_interactive:
                self.cfg.ssh_pubkey_path = str(default)
            else:
                raw = input(f"SSH public key path [{default}]: ").strip()
                self.cfg.ssh_pubkey_path = raw or str(default)
        p = Path(self.cfg.ssh_pubkey_path).expanduser()
        if not p.exists():
            self.die(f"SSH public key not found: {self.cfg.ssh_pubkey_path}")
        self.cfg.ssh_pubkey_value = p.read_text(encoding="utf-8").strip()
        if not self.cfg.ssh_pubkey_value:
            self.die("SSH public key file is empty")

    def prompt_root_domain(self) -> None:
        domains = [d.get("domain", "") for d in self.linode_json("domains", "list") if d.get("domain")]
        if self.cfg.root_domain:
            self.cfg.root_domain_selected_from_linode = self.cfg.root_domain in domains
            if self.cfg.root_domain_selected_from_linode:
                self.info(f"Selected Linode domain: {self.cfg.root_domain}")
            return
        if self.cfg.non_interactive:
            self.die("--non-interactive requires --root-domain")
        if domains:
            print("\nLinode domains:")
            for i, d in enumerate(domains, start=1):
                print(f"{i}) {d}")
            print("Enter number for Linode domain, or type custom domain.")
        while True:
            selection = input("Root domain (e.g. example.com): ").strip().replace(" ", "")
            if not selection:
                self.warn("Root domain is required")
                continue
            if selection.isdigit() and domains:
                idx = int(selection) - 1
                if 0 <= idx < len(domains):
                    self.cfg.root_domain = domains[idx]
                    self.cfg.root_domain_selected_from_linode = True
                    self.info(f"Selected Linode domain: {self.cfg.root_domain}")
                    return
                self.warn(f"Invalid domain selection: {selection}")
                continue
            self.cfg.root_domain = selection
            self.cfg.root_domain_selected_from_linode = False
            return

    def choose_instance_type(self) -> None:
        types = self.linode_json("linodes", "types")
        allowed = [
            t
            for t in types
            if re.match(r"^g6-(nanode|standard)-", t.get("id", ""))
            or re.match(r"^g6-dedicated-[0-9]+$", t.get("id", ""))
            or re.match(r"^g7-dedicated-", t.get("id", ""))
            or re.match(r"^g8-dedicated-", t.get("id", ""))
        ]
        ids = {t.get("id", "") for t in allowed}
        if not ids:
            self.die("No allowed instance types returned from Linode API.")
        self.cfg.plan_types = allowed
        recommended = "g6-standard-2"
        has_recommended = recommended in ids

        if not self.cfg.plan_mode and self.cfg.non_interactive:
            self.cfg.plan_mode = "manual" if self.cfg.instance_type != recommended else "recommended"

        if self.cfg.plan_mode == "recommended":
            if not has_recommended:
                self.die(f"Recommended plan {recommended} not available; set --plan-mode manual --instance-type <id>")
            self.cfg.instance_type = recommended
            self.info(f"Using recommended plan: {recommended}")
            return

        if self.cfg.plan_mode == "manual":
            if self.cfg.instance_type in ids:
                return
            if self.cfg.non_interactive:
                self.die(f"Invalid --instance-type: {self.cfg.instance_type}")

        if self.cfg.plan_mode and self.cfg.plan_mode not in {"recommended", "manual"}:
            self.die(f"Invalid --plan-mode: {self.cfg.plan_mode} (expected recommended|manual)")

        if self.cfg.non_interactive:
            self.cfg.instance_type = recommended if has_recommended else self.cfg.instance_type
            return

        recommended_price = self.plan_price_monthly(recommended)
        recommended_total = recommended_price * 4
        print("\nPlan selection:")
        print(
            f"  1) Deploy recommended config (4 nodes, Shared 4 GB: {recommended}, "
            f"approx ${recommended_total:.2f}/mo)"
        )
        print("  2) Manual plan selection")
        choice = input("Choice [1]: ").strip() or "1"
        if choice == "1" and has_recommended:
            self.cfg.instance_type = recommended
            self.info(f"Using recommended plan: {recommended}")
            return
        while True:
            selected = input(f"Select plan id [{self.cfg.instance_type}]: ").strip() or self.cfg.instance_type
            if selected in ids:
                self.cfg.instance_type = selected
                return
            self.warn(f"Invalid plan id: {selected}")

    def select_dns_mode(self) -> None:
        if self.cfg.dns_mode:
            if self.cfg.dns_mode not in {"linode-dns", "cloudflare-dns", "manual"}:
                self.die(f"Invalid --dns-mode: {self.cfg.dns_mode}")
            return
        if self.cfg.root_domain_selected_from_linode:
            self.cfg.dns_mode = "linode-dns"
            self.info("Auto-selected DNS mode: linode-dns (root domain selected from Linode domains)")
            return
        if self.cfg.non_interactive:
            self.cfg.dns_mode = "manual"
            return
        print("Select DNS mode:\n  1) linode-dns\n  2) cloudflare-dns\n  3) manual")
        choice = input("Choice [3]: ").strip() or "3"
        self.cfg.dns_mode = {"1": "linode-dns", "2": "cloudflare-dns", "3": "manual"}.get(choice, "")
        if not self.cfg.dns_mode:
            self.die("Invalid DNS mode choice")

    def choose_regions(self) -> None:
        self.info("Fetching regions from Linode")
        regions = self.linode_json("regions", "list")
        ids = {r.get("id", "") for r in regions}
        default = {"app": "us-iad", "ord": "us-ord", "iad": "us-iad", "sea": "us-sea"}

        if not self.cfg.region_mode:
            if any([self.cfg.app_region, self.cfg.ord_region, self.cfg.iad_region, self.cfg.sea_region]):
                self.cfg.region_mode = "manual"
            else:
                self.cfg.region_mode = "recommended"

        if self.cfg.region_mode == "recommended":
            self.cfg.app_region, self.cfg.ord_region = default["app"], default["ord"]
            self.cfg.iad_region, self.cfg.sea_region = default["iad"], default["sea"]
        elif self.cfg.region_mode == "manual":
            if self.cfg.non_interactive and not all([self.cfg.app_region, self.cfg.ord_region, self.cfg.iad_region, self.cfg.sea_region]):
                self.die("--region-mode manual requires --app-region/--ord-region/--iad-region/--sea-region")
            if not self.cfg.non_interactive:
                for key in ["app", "ord", "iad", "sea"]:
                    current = getattr(self.cfg, f"{key}_region") or default[key]
                    while True:
                        val = input(f"Select region id for {key} [{current}]: ").strip() or current
                        if val in ids:
                            setattr(self.cfg, f"{key}_region", val)
                            break
                        self.warn(f"Invalid region id: {val}")
        else:
            self.die(f"Invalid --region-mode: {self.cfg.region_mode} (expected recommended|manual)")

        label_map = {r.get("id", ""): r.get("label", r.get("id", "")) for r in regions}
        self.cfg.app_region_label = label_map.get(self.cfg.app_region, self.cfg.app_region)
        self.cfg.ord_region_label = label_map.get(self.cfg.ord_region, self.cfg.ord_region)
        self.cfg.iad_region_label = label_map.get(self.cfg.iad_region, self.cfg.iad_region)
        self.cfg.sea_region_label = label_map.get(self.cfg.sea_region, self.cfg.sea_region)

    def prompt_inputs(self) -> None:
        self.prompt_root_domain()
        if not self.cfg.upright_suffix:
            self.cfg.upright_suffix = f"up.{self.cfg.root_domain}" if self.cfg.non_interactive else (input(f"Upright suffix [up.{self.cfg.root_domain}]: ").strip() or f"up.{self.cfg.root_domain}")

        self.choose_instance_type()
        if self.cfg.provision_mode == "tui":
            self.warn("Provisioning mode 'tui' is deprecated; using classic provisioning")
            self.cfg.provision_mode = "classic"
        if self.cfg.provision_mode != "classic":
            self.cfg.provision_mode = "classic"

        if not self.cfg.non_interactive:
            self.cfg.image = input(f"Image slug [{self.cfg.image}]: ").strip() or self.cfg.image
            self.cfg.deploy_user = input(f"Deploy user [{self.cfg.deploy_user}]: ").strip() or self.cfg.deploy_user
            self.cfg.ssh_port = input(f"SSH port [{self.cfg.ssh_port}]: ").strip() or self.cfg.ssh_port
            self.cfg.registry_server = input(f"Registry server [{self.cfg.registry_server}]: ").strip() or self.cfg.registry_server
            self.cfg.registry_username = self.prompt_required_text(
                "Registry username",
                self.cfg.registry_username,
                disallow={self.PLACEHOLDER_REGISTRY_USERNAME},
            )
            self.sync_image_name_default_from_registry_username()
            self.cfg.image_name = self.prompt_required_text(
                "Image name",
                self.cfg.image_name,
                disallow={self.PLACEHOLDER_IMAGE_NAME},
            )
        else:
            self.sync_image_name_default_from_registry_username()

        self.prompt_local_repo_settings()

        if self.is_placeholder_registry_config():
            if self.cfg.run_deploy == "yes":
                self.die("Set --registry-username and --image-name before --run-deploy")
            self.warn("Using placeholder registry defaults. Set --registry-username and --image-name before deploy.")

        self.select_dns_mode()
        self.choose_regions()

    def is_placeholder_registry_config(self) -> bool:
        return self.cfg.registry_username == self.PLACEHOLDER_REGISTRY_USERNAME or self.image_name_uses_placeholder(self.cfg.image_name)

    def plan_price_monthly(self, plan_id: str) -> float:
        if not self.cfg.plan_types:
            return 0.0
        for row in self.cfg.plan_types:
            if row.get("id") == plan_id:
                return float(row.get("price", {}).get("monthly", 0) or 0)
        return 0.0

    def plan_label(self, plan_id: str) -> str:
        if not self.cfg.plan_types:
            return plan_id
        for row in self.cfg.plan_types:
            if row.get("id") == plan_id:
                memory_gb = int((row.get("memory") or 0) / 1024)
                vcpus = row.get("vcpus")
                disk_gb = int((row.get("disk") or 0) / 1024)
                monthly = row.get("price", {}).get("monthly", 0)
                name = "Linode" if plan_id.startswith("g6-") else "Dedicated"
                return f"{name} {memory_gb} GB ({plan_id}, {vcpus}vCPU, {memory_gb}GB RAM, {disk_gb}GB disk, ${monthly}/mo)"
        return plan_id

    def print_cost_summary(self) -> None:
        each = self.plan_price_monthly(self.cfg.instance_type)
        total = each * 4
        label = self.plan_label(self.cfg.instance_type)
        print("\nDeployment summary (approx monthly cost):")
        for code, region, region_label in [
            ("app", self.cfg.app_region, self.cfg.app_region_label),
            ("ord", self.cfg.ord_region, self.cfg.ord_region_label),
            ("iad", self.cfg.iad_region, self.cfg.iad_region_label),
            ("sea", self.cfg.sea_region, self.cfg.sea_region_label),
        ]:
            print(f"  {code:4} {code}.{self.cfg.upright_suffix:22} {region} ({region_label}) | {label} | ${each:.1f}/mo")
        total_label = self._style("  Approx total (4 nodes):", "1;33")
        total_value = self._style(f" ${total:.2f}/mo", "1;30;43")
        print(f"{total_label}{total_value}")

    def print_topology_summary(self) -> None:
        app = f"[APP] app.{self.cfg.upright_suffix} ({self.cfg.app_region})"
        ord_node = f"[ORD] ord.{self.cfg.upright_suffix} ({self.cfg.ord_region})"
        iad_node = f"[IAD] iad.{self.cfg.upright_suffix} ({self.cfg.iad_region})"
        sea_node = f"[SEA] sea.{self.cfg.upright_suffix} ({self.cfg.sea_region})"
        child_row = f"{ord_node}   {iad_node}   {sea_node}"
        app_pad = max((len(child_row) - len(app)) // 2, 0)
        conn = "/   |   \\"
        conn_pad = max((len(child_row) - len(conn)) // 2, 0)
        print("\nDeployment topology:")
        print(f"{' ' * app_pad}{app}")
        print(f"{' ' * conn_pad}{conn}")
        print(child_row)

    def confirm_plan(self) -> None:
        self.print_cost_summary()
        self.print_topology_summary()
        print(
            f"""

Plan summary:
  Root domain:        {self.cfg.root_domain}
  Upright suffix:     {self.cfg.upright_suffix}
  DNS mode:           {self.cfg.dns_mode}
  Registry server:    {self.cfg.registry_server}
  Registry username:  {self.cfg.registry_username}
  Image name:         {self.cfg.image_name}
  Instance type:      {self.cfg.instance_type}
  Provision mode:     {self.cfg.provision_mode}
  Image:              {self.cfg.image}
  Deploy user:        {self.cfg.deploy_user}
  SSH port:           {self.cfg.ssh_port}
  StackScript label:  {self.cfg.stackscript_label}
  Regions:
    app={self.cfg.app_region} ({self.cfg.app_region_label})
    ord={self.cfg.ord_region} ({self.cfg.ord_region_label})
    iad={self.cfg.iad_region} ({self.cfg.iad_region_label})
    sea={self.cfg.sea_region} ({self.cfg.sea_region_label})
  SSH public key:     {self.cfg.ssh_pubkey_path}
""".rstrip()
        )
        if self.cfg.auto_approve or self.cfg.non_interactive:
            self.info("Proceeding without interactive confirmation")
            return
        yn = input("Proceed? [y/N]: ").strip()
        if not re.match(r"^[Yy]$", yn):
            self.die("Cancelled")
