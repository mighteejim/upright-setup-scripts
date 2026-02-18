from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SetupError(RuntimeError):
    pass


@dataclass
class Config:
    dry_run: bool = False
    resume: bool = False
    destroy: bool = False
    non_interactive: bool = False
    auto_approve: bool = False

    linode_pat: str = ""
    root_domain: str = ""
    upright_suffix: str = ""
    plan_mode: str = ""
    instance_type: str = "g6-standard-2"
    image: str = "linode/ubuntu24.04"
    deploy_user: str = "deploy"
    ssh_port: str = "2222"
    dns_mode: str = ""
    region_mode: str = ""
    registry_server: str = "ghcr.io"
    registry_username: str = "your-github-username"
    image_name: str = "ghcr.io/your-github-username/upright"
    ssh_pubkey_path: str = ""
    ssh_pubkey_value: str = ""
    ssh_key_source: str = ""
    ssh_key_ids: str = ""
    stackscript_label: str = "upright-bootstrap"
    stackscript_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_zone_id: str = ""
    current_phase: str = ""
    run_deploy: str = ""
    deploy_mode: str = "local"
    remote_repo_path: str = ""
    remote_repo_url: str = ""
    local_repo_path: str = ""
    local_repo_url: str = ""
    local_ruby_version: str = ""
    local_repo_created_by_wizard: bool = False
    bootstrap_local_app: bool = False
    pass_prefix: str = "upright"
    manual_dns_confirmed: bool = False
    confirm_destroy: bool = False
    delete_stackscript: bool = False
    output_json: str = ""

    app_region: str = ""
    ord_region: str = ""
    iad_region: str = ""
    sea_region: str = ""
    app_region_label: str = ""
    ord_region_label: str = ""
    iad_region_label: str = ""
    sea_region_label: str = ""
    plan_types: list[dict[str, Any]] | None = None
    provision_mode: str = "classic"
    root_domain_selected_from_linode: bool = False
