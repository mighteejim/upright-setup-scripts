from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import shlex
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

class DeployOps:
    def render_password_report(self) -> str:
        rows = self.password_rows()
        if not rows:
            return ""
        table_rows = [
            [r["code"], r["linode_id"], r["fqdn"], r["root_password"], r["deploy_password"]]
            for r in rows
        ]
        table = self.render_table(
            ["code", "linode_id", "fqdn", "root_password", "deploy_password"],
            table_rows,
        )
        return (
            "Upright node passwords\n"
            f"Generated at: {self.now_iso()}\n"
            f"Root domain: {self.cfg.root_domain}\n"
            f"Deploy user: {self.cfg.deploy_user}\n"
            "WARNING: plaintext credentials, store securely and rotate after handoff.\n\n"
            f"{table}\n"
        )

    def write_local_password_report(self, repo_path: Path | None = None) -> Path | None:
        report = self.render_password_report()
        if not report:
            self.warn("No captured node passwords available; skipping local password report")
            return None
        target_repo = repo_path or self.local_repo_dir()
        target_repo.mkdir(parents=True, exist_ok=True)
        out_path = target_repo / "linode-passwords.txt"
        out_path.write_text(report, encoding="utf-8")
        self.info(f"Wrote password report to {self.local_repo_display(out_path)}")
        return out_path

    def write_remote_password_report(self, repo_path: str) -> None:
        report = self.render_password_report()
        if not report:
            self.warn("No captured node passwords available; skipping remote password report")
            return
        remote_path = f"{repo_path.rstrip('/')}/linode-passwords.txt"
        self._ssh_run(f"cat > {shlex.quote(remote_path)}", input_text=report)
        self.info(f"Wrote password report to remote repo path: {remote_path}")

    def local_load_secrets_script(self) -> str:
        return """#!/usr/bin/env bash
set -euo pipefail

PASS_PREFIX="${PASS_PREFIX:-upright}"

read_secret() {
  local key="$1"
  local env_name="$2"
  local env_val="${!env_name:-}"
  if [[ -n "${env_val}" ]]; then
    printf '%s' "${env_val}"
    return 0
  fi
  if ! command -v pass >/dev/null 2>&1; then
    return 1
  fi
  pass show "${PASS_PREFIX}/${key}" 2>/dev/null | head -n1
}

require_secret() {
  local key="$1"
  local env_name="$2"
  local label="$3"
  local value
  value="$(read_secret "${key}" "${env_name}" || true)"
  if [[ -z "${value}" ]]; then
    echo "[ERROR] Missing secret: ${label}. Run bin/setup-pass-secrets or export ${env_name}." >&2
    exit 1
  fi
  printf '%s' "${value}"
}

KAMAL_REGISTRY_PASSWORD="$(require_secret kamal/registry_password KAMAL_REGISTRY_PASSWORD KAMAL_REGISTRY_PASSWORD)"
ADMIN_PASSWORD="$(require_secret admin_password ADMIN_PASSWORD ADMIN_PASSWORD)"

cat <<EOF
export KAMAL_REGISTRY_PASSWORD=$(printf %q "${KAMAL_REGISTRY_PASSWORD}")
export ADMIN_PASSWORD=$(printf %q "${ADMIN_PASSWORD}")
EOF
"""

    def local_setup_pass_secrets_script(self) -> str:
        return """#!/usr/bin/env bash
set -euo pipefail

PASS_PREFIX="${PASS_PREFIX:-upright}"
DEFAULT_NAME="${UPRIGHT_GPG_NAME:-Upright Deploy}"
DEFAULT_EMAIL="${UPRIGHT_GPG_EMAIL:-deploy@$(hostname -f 2>/dev/null || hostname)}"

need_cmd() {
  local name="$1"
  if command -v "${name}" >/dev/null 2>&1; then
    return 0
  fi
  echo "[ERROR] Missing required command after install attempt: ${name}" >&2
  case "$(uname -s)" in
    Darwin)
      echo "Install with: brew install gnupg pass pinentry-mac" >&2
      ;;
    Linux)
      echo "Install with your distro package manager, e.g. sudo apt-get install -y gnupg2 pass pinentry-curses" >&2
      ;;
  esac
  exit 1
}

install_prereqs() {
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        echo "[ERROR] Homebrew is required for automatic install on macOS." >&2
        echo "Install Homebrew from https://brew.sh and re-run." >&2
        return 1
      fi
      echo "[INFO] Installing prerequisites via brew: gnupg pass pinentry-mac"
      brew install gnupg pass pinentry-mac
      ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        echo "[INFO] Installing prerequisites via apt-get: gnupg2 pass pinentry-curses"
        sudo apt-get update -y
        sudo apt-get install -y gnupg2 pass pinentry-curses
        return 0
      fi
      if command -v dnf >/dev/null 2>&1; then
        echo "[INFO] Installing prerequisites via dnf: gnupg2 pass pinentry"
        sudo dnf install -y gnupg2 pass pinentry
        return 0
      fi
      if command -v yum >/dev/null 2>&1; then
        echo "[INFO] Installing prerequisites via yum: gnupg2 pass pinentry"
        sudo yum install -y gnupg2 pass pinentry
        return 0
      fi
      if command -v pacman >/dev/null 2>&1; then
        echo "[INFO] Installing prerequisites via pacman: gnupg pass pinentry"
        sudo pacman -Sy --noconfirm gnupg pass pinentry
        return 0
      fi
      echo "[ERROR] Unsupported Linux package manager for automatic install." >&2
      return 1
      ;;
    *)
      echo "[ERROR] Unsupported OS for automatic install: $(uname -s)" >&2
      return 1
      ;;
  esac
}

AUTO_INSTALL_ATTEMPTED=0
ensure_cmd() {
  local name="$1"
  if command -v "${name}" >/dev/null 2>&1; then
    return 0
  fi
  echo "[WARN] Missing required command: ${name}"
  if [[ "${AUTO_INSTALL_ATTEMPTED}" != "1" ]]; then
    AUTO_INSTALL_ATTEMPTED=1
    install_prereqs || true
  fi
  need_cmd "${name}"
}

ensure_cmd gpg
ensure_cmd pass
if [[ -t 0 ]]; then
  export GPG_TTY="$(tty)"
fi

echo "Upright pass bootstrap (prefix: ${PASS_PREFIX})"

list_secret_keys() {
  gpg --list-secret-keys --with-colons 2>/dev/null | awk -F: '
    $1=="sec" {
      if (kid != "") {
        print kid "|" uid
      }
      kid=""
      uid=""
      want_fpr=1
      next
    }
    want_fpr && $1=="fpr" {
      kid=$10
      want_fpr=0
      next
    }
    $1=="uid" && kid!="" && uid=="" {
      uid=$10
      next
    }
    END {
      if (kid != "") {
        print kid "|" uid
      }
    }
  '
}

latest_secret_key_id() {
  gpg --list-secret-keys --with-colons 2>/dev/null | awk -F: '
    $1=="sec" { want_fpr=1; next }
    want_fpr && $1=="fpr" { id=$10; want_fpr=0; next }
    END { print id }
  '
}

create_new_key() {
  local gpg_name gpg_email key_id
  read -r -p "GPG name [${DEFAULT_NAME}]: " gpg_name
  read -r -p "GPG email [${DEFAULT_EMAIL}]: " gpg_email
  gpg_name="${gpg_name:-${DEFAULT_NAME}}"
  gpg_email="${gpg_email:-${DEFAULT_EMAIL}}"
  echo "Generating key for ${gpg_name} <${gpg_email}>" >&2
  echo "You may be prompted for a passphrase in pinentry." >&2
  gpg --quick-generate-key "${gpg_name} <${gpg_email}>" default default 0 >/dev/null
  key_id="$(latest_secret_key_id)"
  if [[ -z "${key_id}" ]]; then
    echo "[ERROR] Could not resolve newly created GPG key id." >&2
    exit 1
  fi
  printf '%s' "${key_id}"
}

choose_existing_key() {
  local rows key_count line key_id key_uid idx choice selected
  local -a key_ids
  local -a key_uids
  rows="$(list_secret_keys || true)"
  key_count=0
  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    key_id="${line%%|*}"
    key_uid="${line#*|}"
    [[ -z "${key_id}" ]] && continue
    [[ "${key_uid}" == "${line}" || -z "${key_uid}" ]] && key_uid="(no uid)"
    key_ids+=("${key_id}")
    key_uids+=("${key_uid}")
    key_count=$((key_count + 1))
  done <<< "${rows}"

  if [[ "${key_count}" -eq 0 ]]; then
    echo "[ERROR] No existing secret keys found." >&2
    return 1
  fi

  echo "Available existing secret keys:" >&2
  idx=1
  while [[ "${idx}" -le "${key_count}" ]]; do
    echo "  ${idx}) ${key_ids[$((idx - 1))]}  ${key_uids[$((idx - 1))]}" >&2
    idx=$((idx + 1))
  done

  if [[ "${key_count}" -eq 1 ]]; then
    selected=1
  else
    while true; do
      read -r -p "Select key [1-${key_count}] [1]: " choice
      choice="${choice:-1}"
      if [[ "${choice}" =~ ^[0-9]+$ ]] && [[ "${choice}" -ge 1 ]] && [[ "${choice}" -le "${key_count}" ]]; then
        selected="${choice}"
        break
      fi
      echo "[WARN] Invalid selection: ${choice}" >&2
    done
  fi
  printf '%s' "${key_ids[$((selected - 1))]}"
}

verify_key_unlock() {
  local key_id="$1"
  local probe_file sig_file
  probe_file="$(mktemp "${TMPDIR:-/tmp}/upright-gpg-probe.XXXXXX")"
  sig_file="${probe_file}.asc"
  printf 'upright gpg unlock probe\\n' > "${probe_file}"
  echo "Validating key access for ${key_id}. Enter passphrase when prompted."
  if gpg --yes --armor --local-user "${key_id}" --detach-sign --output "${sig_file}" "${probe_file}" >/dev/null 2>&1; then
    rm -f "${probe_file}" "${sig_file}"
    return 0
  fi
  rm -f "${probe_file}" "${sig_file}"
  return 1
}

KEY_ID=""
KEY_SOURCE=""
if gpg --list-secret-keys --with-colons | grep -q '^sec:'; then
  echo "Secret GPG key(s) found." >&2
  while true; do
    echo "Key setup mode:" >&2
    echo "  1) Use existing secret key" >&2
    echo "  2) Create a new secret key" >&2
    read -r -p "Choice [1]: " key_mode
    key_mode="${key_mode:-1}"
    if [[ "${key_mode}" == "1" ]]; then
      KEY_ID="$(choose_existing_key || true)"
      [[ -n "${KEY_ID}" ]] || continue
      KEY_SOURCE="existing"
      break
    fi
    if [[ "${key_mode}" == "2" ]]; then
      KEY_ID="$(create_new_key)"
      KEY_SOURCE="new"
      break
    fi
    echo "[WARN] Invalid key mode: ${key_mode}" >&2
  done
else
  echo "No secret GPG key found; creating a new key." >&2
  KEY_ID="$(create_new_key)"
  KEY_SOURCE="new"
fi

if [[ "${KEY_SOURCE}" == "existing" ]]; then
  while true; do
    if verify_key_unlock "${KEY_ID}"; then
      break
    fi
    read -r -p "Could not unlock key ${KEY_ID}. Retry? [Y/n]: " retry_unlock
    retry_unlock="${retry_unlock:-Y}"
    if [[ ! "${retry_unlock}" =~ ^[Yy]$ ]]; then
      echo "Aborting: unable to unlock selected key ${KEY_ID}." >&2
      exit 1
    fi
  done
fi

pass init "${KEY_ID}" >/dev/null 2>&1 || true
echo "Using GPG key id: ${KEY_ID}"

store_secret() {
  local key="$1"
  local env_name="$2"
  local prompt="$3"
  local hidden="${4:-yes}"
  local entry="${PASS_PREFIX}/${key}"
  local value="${!env_name:-}"
  if pass show "${entry}" >/dev/null 2>&1; then
    read -r -p "${entry} exists. Overwrite? [y/N]: " overwrite
    if [[ ! "${overwrite}" =~ ^[Yy]$ ]]; then
      echo "Skipping ${entry}"
      return 0
    fi
  fi
  if [[ -z "${value}" ]]; then
    if [[ "${hidden}" == "yes" ]]; then
      read -r -s -p "${prompt}: " value
      echo
    else
      read -r -p "${prompt}: " value
    fi
  fi
  if [[ -z "${value}" ]]; then
    echo "[ERROR] ${prompt} is required." >&2
    exit 1
  fi
  printf '%s\\n' "${value}" | pass insert -m -f "${entry}" >/dev/null
  echo "Saved ${entry}"
}

store_secret "kamal/registry_password" "KAMAL_REGISTRY_PASSWORD" "Kamal registry password" "yes"
store_secret "admin_password" "ADMIN_PASSWORD" "Admin password" "yes"

echo
echo "Secrets stored in pass."
echo "Next:"
echo "  eval \"$(bin/load-secrets)\""
echo "  bin/kamal setup"
echo "  bin/kamal deploy"
"""

    def ensure_local_secret_scripts(self, repo_path: Path) -> None:
        load_path = repo_path / "bin/load-secrets"
        setup_path = repo_path / "bin/setup-pass-secrets"
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would write {self.local_repo_display(load_path)}")
            self.info(f"DRY-RUN: would write {self.local_repo_display(setup_path)}")
            return

        (repo_path / "bin").mkdir(parents=True, exist_ok=True)
        scripts = [
            (load_path, self.local_load_secrets_script()),
            (setup_path, self.local_setup_pass_secrets_script()),
        ]
        for path, content in scripts:
            existing = path.read_text(encoding="utf-8") if path.exists() else None
            if existing is None:
                path.write_text(content, encoding="utf-8")
                self.info(f"Wrote helper script: {self.local_repo_display(path)}")
            elif existing != content:
                path.write_text(content, encoding="utf-8")
                self.info(f"Updated helper script: {self.local_repo_display(path)}")
            if not os.access(path, os.X_OK):
                path.chmod(path.stat().st_mode | 0o111)
                self.info(f"Set executable bit on {self.local_repo_display(path)}")

    def write_config_files(self) -> None:
        self.info("Preparing local app repo and generating deploy config files")
        self.set_phase("config_generating")
        repo_path = self.ensure_local_app_repo()
        if self.cfg.dry_run:
            self.info(f"DRY-RUN: would write {self.local_repo_display(repo_path / 'config/deploy.yml')}")
            self.info(f"DRY-RUN: would write {self.local_repo_display(repo_path / 'config/sites.yml')}")
            self.info(f"DRY-RUN: would write {self.local_repo_display(repo_path / '.kamal/secrets')}")
            self.ensure_local_secret_scripts(repo_path)
            self.set_phase("config_planned")
            return

        app, ord_ip, iad, sea = self.node_ipv4("app"), self.node_ipv4("ord"), self.node_ipv4("iad"), self.node_ipv4("sea")
        if not all([app, ord_ip, iad, sea]):
            self.die("Missing node IP data for deploy.yml generation")
        self.info(
            "Config inputs: "
            f"domain={self.cfg.root_domain} suffix={self.cfg.upright_suffix} "
            f"nodes=app:{app} ord:{ord_ip} iad:{iad} sea:{sea}"
        )

        app_fqdn, ord_fqdn, iad_fqdn, sea_fqdn = self.fqdn("app"), self.fqdn("ord"), self.fqdn("iad"), self.fqdn("sea")
        (repo_path / "config").mkdir(parents=True, exist_ok=True)
        (repo_path / ".kamal").mkdir(parents=True, exist_ok=True)
        self.info(f"Rendering templates in {self.local_repo_display(repo_path)}")

        deploy_yml = f"""service: upright
image: {self.cfg.image_name}

servers:
  web:
    hosts:
      - {app_fqdn}: [admin]
      - {ord_ip}: [ord]
      - {iad}: [iad]
      - {sea}: [sea]
  jobs:
    hosts:
      - {ord_ip}: [ord]
      - {iad}: [iad]
      - {sea}: [sea]
    cmd: bin/jobs

ssh:
  user: {self.cfg.deploy_user}
  port: {self.cfg.ssh_port}

registry:
  server: {self.cfg.registry_server}
  username:
    - KAMAL_REGISTRY_USERNAME
  password:
    - KAMAL_REGISTRY_PASSWORD

builder:
  arch: amd64

proxy:
  app_port: 3000
  ssl: false
  forward_headers: true
  run:
    http_port: 8080
    https_port: 8443
  hosts:
    - {app_fqdn}
    - {ord_fqdn}
    - {iad_fqdn}
    - {sea_fqdn}

env:
  clear:
    UPRIGHT_HOSTNAME: {self.cfg.upright_suffix}
    PLAYWRIGHT_SERVER_URL: ws://upright-playwright:53333/playwright
    PROMETHEUS_URL: http://upright-prometheus:9090
    ALERTMANAGER_URL: http://upright-alertmanager:9093
  secret:
    - RAILS_MASTER_KEY
    - ADMIN_PASSWORD
  tags:
    admin:
      SITE_SUBDOMAIN: app
    ord:
      SITE_SUBDOMAIN: ord
    iad:
      SITE_SUBDOMAIN: iad
    sea:
      SITE_SUBDOMAIN: sea

volumes:
  - "upright_storage:/rails/storage"

accessories:
  playwright:
    image: jacoblincool/playwright:chromium-server-1.55.0
    port: "127.0.0.1:53333:53333"
    roles:
      - jobs

  prometheus:
    image: prom/prometheus:v3.2.1
    host: {app_fqdn}
    cmd: >-
      --config.file=/etc/prometheus/prometheus.yml
      --storage.tsdb.path=/prometheus
      --storage.tsdb.retention.time=30d
      --web.enable-otlp-receiver
    files:
      - config/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - config/prometheus/rules/upright.rules.yml:/etc/prometheus/rules/upright.rules.yml
    volumes:
      - prometheus_data:/prometheus

  alertmanager:
    image: prom/alertmanager:v0.28.1
    host: {app_fqdn}
    cmd: --config.file=/etc/alertmanager/alertmanager.yml
    files:
      - config/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml
    volumes:
      - alertmanager_data:/alertmanager

aliases:
  console: app exec -i "bin/rails console"
  logs: app logs -f

retain_containers: 3
"""
        sites_yml = """shared:
  sites:
    - code: ord
      city: Chicago
      country: US
      geohash: dp3wnp

    - code: iad
      city: Washington
      country: US
      geohash: dqcjqb

    - code: sea
      city: Seattle
      country: US
      geohash: c23nb6
"""
        secrets_tmpl = f"""# Secrets defined here are available for reference under registry/password, env/secret, builder/secrets,
# and accessories/*/env/secret in config/deploy.yml. All secrets should be pulled from either
# password manager, ENV, or a file. DO NOT ENTER RAW CREDENTIALS HERE! This file needs to be safe for git.

KAMAL_REGISTRY_USERNAME={self.cfg.registry_username}
KAMAL_REGISTRY_PASSWORD=$KAMAL_REGISTRY_PASSWORD
RAILS_MASTER_KEY=$(cat config/master.key)
ADMIN_PASSWORD=$ADMIN_PASSWORD
"""
        (repo_path / "config/deploy.yml").write_text(deploy_yml, encoding="utf-8")
        (repo_path / "config/sites.yml").write_text(sites_yml, encoding="utf-8")
        (repo_path / ".kamal/secrets").write_text(secrets_tmpl, encoding="utf-8")
        self.ensure_local_secret_scripts(repo_path)
        self.set_phase("config_generated")
        self.info(
            "Wrote deploy config files: "
            f"{self.local_repo_display(repo_path / 'config/deploy.yml')}, "
            f"{self.local_repo_display(repo_path / 'config/sites.yml')}, "
            f"{self.local_repo_display(repo_path / '.kamal/secrets')}"
        )

    def _ssh_target(self) -> str:
        return f"{self.cfg.deploy_user}@{self.fqdn('app')}"

    def _ssh_run(
        self,
        remote_cmd: str,
        *,
        capture: bool = False,
        check: bool = True,
        input_text: str | None = None,
    ) -> str:
        return self.run(
            ["ssh", "-p", self.cfg.ssh_port, self._ssh_target(), "bash", "-lc", remote_cmd],
            capture=capture,
            check=check,
            input_text=input_text,
        )

    def _remote_bootstrap_script(self) -> str:
        return """#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${1:-$HOME/upright}"
PASS_PREFIX="${2:-upright}"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y >/dev/null 2>&1 || true
  sudo apt-get install -y gnupg2 pass >/dev/null 2>&1 || true
fi

if ! gpg --list-secret-keys --with-colons | grep -q '^sec:'; then
  gpg --batch --yes --pinentry-mode loopback --passphrase '' \
    --quick-generate-key "Upright Deploy <deploy@$(hostname -f 2>/dev/null || hostname)>" default default 0
fi

KEY_ID="$(gpg --list-secret-keys --with-colons | awk -F: '/^sec:/ {print $5; exit}')"
if [[ -n "${KEY_ID}" ]]; then
  pass init "${KEY_ID}" >/dev/null 2>&1 || true
fi

mkdir -p "${REPO_PATH}/bin"
cat > "${REPO_PATH}/bin/load-secrets" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
PASS_PREFIX="${PASS_PREFIX:-upright}"
read_secret() {
  local key="$1"
  pass show "${PASS_PREFIX}/${key}" 2>/dev/null | head -n1
}
cat <<EOF
export KAMAL_REGISTRY_PASSWORD=$(printf %q "$(read_secret kamal/registry_password)")
export ADMIN_PASSWORD=$(printf %q "$(read_secret admin_password)")
EOF
EOS
chmod +x "${REPO_PATH}/bin/load-secrets"

echo "Remote bootstrap ready: REPO_PATH=${REPO_PATH} PASS_PREFIX=${PASS_PREFIX}"
"""

    def _ensure_remote_bootstrap_script(self) -> None:
        exists = self._ssh_run(
            "if command -v upright-remote-pass-bootstrap >/dev/null 2>&1; then echo yes; else echo no; fi",
            capture=True,
        ).strip()
        if exists == "yes":
            return

        script = self._remote_bootstrap_script()
        self._ssh_run("cat > /tmp/upright-remote-pass-bootstrap", input_text=script)
        self._ssh_run(
            "sudo install -m 0755 /tmp/upright-remote-pass-bootstrap /usr/local/bin/upright-remote-pass-bootstrap "
            "&& rm -f /tmp/upright-remote-pass-bootstrap"
        )

    def _ensure_remote_repo(self, repo_path: str) -> None:
        quoted_path = shlex.quote(repo_path)
        if self.cfg.remote_repo_url:
            quoted_url = shlex.quote(self.cfg.remote_repo_url)
            self._ssh_run(
                f"if [ ! -d {quoted_path} ]; then git clone --depth 1 {quoted_url} {quoted_path}; fi"
            )
        else:
            has_repo = self._ssh_run(
                f"if [ -d {quoted_path} ]; then echo yes; else echo no; fi",
                capture=True,
            ).strip()
            if has_repo != "yes":
                self.die(
                    f"Remote repo path missing on app node: {repo_path}. "
                    "Set --remote-repo-url to auto-clone."
                )

    def _seed_remote_pass_entry(self, key: str, env_var: str, prompt: str) -> None:
        entry = f"{self.cfg.pass_prefix}/{key}"
        quoted_entry = shlex.quote(entry)
        exists = self._ssh_run(
            f"if pass show {quoted_entry} >/dev/null 2>&1; then echo yes; else echo no; fi",
            capture=True,
        ).strip()
        if exists == "yes":
            return

        value = os.environ.get(env_var, "").strip()
        if not value:
            if self.cfg.non_interactive:
                self.die(f"Missing {env_var} for remote pass bootstrap")
            value = getpass.getpass(prompt).strip()
        if not value:
            self.die(f"Missing value for pass entry: {entry}")

        self._ssh_run(f"pass insert -m {quoted_entry}", input_text=value + "\n")
        self.info(f"Seeded remote pass entry: {entry}")

    def _run_remote_pass_deploy(self) -> None:
        self.check_dependency("ssh")
        repo_path = self.cfg.remote_repo_path or f"/home/{self.cfg.deploy_user}/upright"
        pass_prefix = self.cfg.pass_prefix or "upright"

        self.info(f"Remote deploy mode: remote-pass (target={self._ssh_target()})")
        self._ensure_remote_bootstrap_script()
        self._ensure_remote_repo(repo_path)

        self._ssh_run(
            f"/usr/local/bin/upright-remote-pass-bootstrap {shlex.quote(repo_path)} {shlex.quote(pass_prefix)}"
        )

        self._seed_remote_pass_entry(
            "kamal/registry_password",
            "KAMAL_REGISTRY_PASSWORD",
            "Remote secret KAMAL_REGISTRY_PASSWORD: ",
        )
        self._seed_remote_pass_entry(
            "admin_password",
            "ADMIN_PASSWORD",
            "Remote secret ADMIN_PASSWORD: ",
        )

        self.set_phase("deploying")
        deploy_cmd = (
            f"cd {shlex.quote(repo_path)} && "
            f"PASS_PREFIX={shlex.quote(pass_prefix)} eval \"$(bin/load-secrets)\" && "
            "bin/kamal setup && bin/kamal deploy"
        )
        self._ssh_run(deploy_cmd)
        self.set_phase("deployed")
        self.write_local_password_report(self.local_repo_dir())
        self.write_remote_password_report(repo_path)

    def ensure_local_deploy_secrets(self, repo_path: Path) -> None:
        pass_prefix = self.cfg.pass_prefix or "upright"
        load_cmd = f"cd {shlex.quote(str(repo_path))} && PASS_PREFIX={shlex.quote(pass_prefix)} bin/load-secrets"

        self.info("Checking local deploy secrets via bin/load-secrets")
        probe = subprocess.run(["bash", "-lc", load_cmd], cwd=self.cwd, capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            self.info("Local deploy secrets resolved via bin/load-secrets")
            return

        detail = (probe.stderr or probe.stdout).strip()
        if detail:
            self.warn(f"bin/load-secrets precheck failed: {detail}")

        if self.cfg.non_interactive:
            detail = detail or "bin/load-secrets failed"
            self.die(f"Missing local deploy secrets in non-interactive mode: {detail}")

        self.info("Initializing local deploy secrets via bin/setup-pass-secrets (interactive)")
        self.info("This may install gpg/pass and prompt for GPG key selection, key unlock, and secret values")
        setup_cmd = (
            f"cd {shlex.quote(str(repo_path))} && "
            f"PASS_PREFIX={shlex.quote(pass_prefix)} bin/setup-pass-secrets"
        )
        setup_proc = subprocess.run(["bash", "-lc", setup_cmd], cwd=self.cwd, check=False)
        if setup_proc.returncode != 0:
            self.die(f"bin/setup-pass-secrets failed (exit {setup_proc.returncode})")

        self.info("Re-validating local deploy secrets via bin/load-secrets")
        verify = subprocess.run(["bash", "-lc", load_cmd], cwd=self.cwd, capture_output=True, text=True, check=False)
        if verify.returncode != 0:
            detail = (verify.stderr or verify.stdout).strip() or "bin/load-secrets failed"
            self.die(f"Local deploy secrets validation failed after setup: {detail}")
        self.info("Local deploy secrets validated")

    def run_kamal_deploy(self) -> None:
        if self.cfg.dry_run:
            self.info('DRY-RUN: would run eval "$(bin/load-secrets)"')
            self.info("DRY-RUN: would run bin/kamal setup")
            self.info("DRY-RUN: would run bin/kamal deploy")
            return

        deploy_mode = self.cfg.deploy_mode or "local"
        if deploy_mode == "remote-pass":
            if self.cfg.run_deploy == "yes":
                pass
            elif self.cfg.run_deploy == "no" or self.cfg.non_interactive:
                self.warn("Skipped kamal deploy")
                return
            else:
                yn = input("Run kamal setup + deploy now? [y/N]: ").strip()
                if not re.match(r"^[Yy]$", yn):
                    self.warn("Skipped kamal deploy by user choice")
                    return
            self._run_remote_pass_deploy()
            return

        repo_path = self.ensure_local_app_repo()
        missing: list[str] = []
        if not self.repo_has_executable(repo_path, "bin/setup-pass-secrets"):
            missing.append("bin/setup-pass-secrets")
        if not self.repo_has_executable(repo_path, "bin/load-secrets"):
            missing.append("bin/load-secrets")
        if not self.repo_has_executable(repo_path, "bin/kamal"):
            missing.append("bin/kamal")
        if missing:
            self.warn(f"Skipping automated deploy; missing prerequisite(s): {', '.join(missing)}")
            self.show_local_bootstrap_steps(repo_path)
            return

        if self.cfg.run_deploy == "no" or (self.cfg.non_interactive and self.cfg.run_deploy != "yes"):
            self.warn("Skipped kamal deploy")
            return

        self.ensure_local_deploy_secrets(repo_path)

        if self.cfg.run_deploy != "yes":
            yn = input("Run kamal setup + deploy now? [y/N]: ").strip()
            if not re.match(r"^[Yy]$", yn):
                self.warn("Skipped kamal deploy by user choice")
                return

        self.set_phase("deploying")
        pass_prefix = self.cfg.pass_prefix or "upright"
        self.info(f"Running local Kamal deploy via rbenv Ruby in {self.local_repo_display(repo_path)}")
        deploy_script = (
            f"PASS_PREFIX={shlex.quote(pass_prefix)} eval \"$(bin/load-secrets)\"\n"
            "bin/kamal setup\n"
            "bin/kamal deploy"
        )
        self.run_with_rbenv(repo_path, deploy_script, progress_label="kamal setup + deploy")
        self.set_phase("deployed")
        self.write_local_password_report(repo_path)

    def show_local_bootstrap_steps(self, repo_path: Path) -> None:
        repo_disp = self.local_repo_display(repo_path)
        ruby_version = self.preferred_ruby_version()
        print(
            f"""

Local app bootstrap (suggested):
1. Ensure Ruby toolchain via rbenv
   brew install rbenv ruby-build
   rbenv install -s {ruby_version}
   rbenv local {ruby_version}

2. Bootstrap app repo
   cd {repo_disp}
   bundle add upright
   bundle exec rails db:prepare

3. Initialize local secret store
   cd {repo_disp}
   bin/setup-pass-secrets

4. Deploy from local repo
   eval "$(bin/load-secrets)"
   bin/kamal setup
   bin/kamal deploy
""".rstrip()
        )

    def post_deploy_checks(self) -> None:
        if self.cfg.dry_run:
            self.info("DRY-RUN: would run post-deploy endpoint checks")
            return
        self.info("Running basic endpoint checks")
        failures = 0
        for code in ["app", "ord", "iad", "sea"]:
            fqdn = self.fqdn(code)
            proc = subprocess.run(["curl", "-fsS", "--max-time", "20", f"https://{fqdn}"], cwd=self.cwd, capture_output=True, text=True)
            if proc.returncode == 0:
                self.info(f"HTTP OK: https://{fqdn}")
            else:
                self.warn(f"HTTP check failed: https://{fqdn}")
                failures += 1
        if failures:
            self.warn(f"Post-deploy checks completed with {failures} failure(s)")
        else:
            self.info("Post-deploy checks passed")

    def show_next_steps(self) -> None:
        print(
            """

Setup flow complete:
- StackScript synced
- Linodes provisioned
- DNS configured and verified
- Config files generated

If deploy was skipped, run:
  bin/setup-pass-secrets
  eval "$(bin/load-secrets)"
  bin/kamal setup
  bin/kamal deploy
""".rstrip()
        )
