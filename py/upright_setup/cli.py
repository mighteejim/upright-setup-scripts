from __future__ import annotations

import argparse

from .models import Config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="upright-linode-setup.py",
        description="Guided setup for Upright on Linode (Python implementation).",
        add_help=True,
    )

    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--destroy", action="store_true")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--run-deploy", action="store_true")
    p.add_argument("--skip-deploy", action="store_true")
    p.add_argument("--deploy-mode")
    p.add_argument("--remote-repo-path")
    p.add_argument("--remote-repo-url")
    p.add_argument("--local-repo-path")
    p.add_argument("--local-repo-url")
    p.add_argument("--local-ruby-version")
    p.add_argument("--bootstrap-local-app", action="store_true")
    p.add_argument("--pass-prefix")
    p.add_argument("--manual-dns-confirmed", action="store_true")
    p.add_argument("--confirm-destroy", action="store_true")
    p.add_argument("--delete-stackscript", action="store_true")

    p.add_argument("--linode-pat")
    p.add_argument("--root-domain")
    p.add_argument("--upright-suffix")
    p.add_argument("--plan-mode")
    p.add_argument("--instance-type")
    p.add_argument("--provision-mode")
    p.add_argument("--image")
    p.add_argument("--deploy-user")
    p.add_argument("--ssh-port")
    p.add_argument("--registry-server")
    p.add_argument("--registry-username")
    p.add_argument("--image-name")
    p.add_argument("--dns-mode")
    p.add_argument("--region-mode")
    p.add_argument("--app-region")
    p.add_argument("--ord-region")
    p.add_argument("--iad-region")
    p.add_argument("--sea-region")
    p.add_argument("--ssh-key-source")
    p.add_argument("--ssh-key-ids")
    p.add_argument("--ssh-pubkey-path")
    p.add_argument("--cloudflare-token")
    p.add_argument("--output-json")
    p.add_argument("--stackscript-label")
    return p


def parse_config(argv: list[str]) -> Config:
    args = build_parser().parse_args(argv)
    cfg = Config()

    cfg.dry_run = args.dry_run
    cfg.resume = args.resume
    cfg.destroy = args.destroy
    cfg.non_interactive = args.non_interactive
    cfg.auto_approve = args.yes

    cfg.manual_dns_confirmed = args.manual_dns_confirmed
    cfg.confirm_destroy = args.confirm_destroy
    cfg.delete_stackscript = args.delete_stackscript
    cfg.bootstrap_local_app = args.bootstrap_local_app

    if args.run_deploy:
        cfg.run_deploy = "yes"
    if args.skip_deploy:
        cfg.run_deploy = "no"

    for key, val in {
        "linode_pat": args.linode_pat,
        "root_domain": args.root_domain,
        "upright_suffix": args.upright_suffix,
        "plan_mode": args.plan_mode,
        "instance_type": args.instance_type,
        "provision_mode": args.provision_mode,
        "image": args.image,
        "deploy_user": args.deploy_user,
        "ssh_port": args.ssh_port,
        "registry_server": args.registry_server,
        "registry_username": args.registry_username,
        "image_name": args.image_name,
        "dns_mode": args.dns_mode,
        "region_mode": args.region_mode,
        "app_region": args.app_region,
        "ord_region": args.ord_region,
        "iad_region": args.iad_region,
        "sea_region": args.sea_region,
        "ssh_key_source": args.ssh_key_source,
        "ssh_key_ids": args.ssh_key_ids,
        "ssh_pubkey_path": args.ssh_pubkey_path,
        "cloudflare_api_token": args.cloudflare_token,
        "output_json": args.output_json,
        "stackscript_label": args.stackscript_label,
        "deploy_mode": args.deploy_mode,
        "remote_repo_path": args.remote_repo_path,
        "remote_repo_url": args.remote_repo_url,
        "local_repo_path": args.local_repo_path,
        "local_repo_url": args.local_repo_url,
        "local_ruby_version": args.local_ruby_version,
        "pass_prefix": args.pass_prefix,
    }.items():
        if val is not None:
            setattr(cfg, key, val)

    count = 0
    count += 1 if cfg.resume else 0
    count += 1 if cfg.destroy else 0
    if count > 1:
        raise ValueError("Use only one of --resume or --destroy")

    cfg.plan_mode = cfg.plan_mode.lower()
    cfg.region_mode = cfg.region_mode.lower()
    cfg.dns_mode = cfg.dns_mode.lower()
    cfg.ssh_key_source = cfg.ssh_key_source.lower()
    cfg.run_deploy = cfg.run_deploy.lower()
    cfg.deploy_mode = cfg.deploy_mode.lower()
    if cfg.deploy_mode != "remote-pass":
        cfg.bootstrap_local_app = True

    if cfg.deploy_mode and cfg.deploy_mode not in {"local", "remote-pass"}:
        raise ValueError(f"Invalid --deploy-mode: {cfg.deploy_mode} (expected local|remote-pass)")

    if cfg.provision_mode not in {"classic", "tui"}:
        raise ValueError(f"Invalid --provision-mode: {cfg.provision_mode} (expected classic)")
    if cfg.provision_mode == "tui":
        cfg.provision_mode = "classic"

    if not cfg.ssh_port.isdigit():
        raise ValueError(f"Invalid --ssh-port: {cfg.ssh_port}")

    return cfg
