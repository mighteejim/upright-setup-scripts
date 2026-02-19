#!/usr/bin/env bash
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
SSH_PORT="${SSH_PORT:-2222}"
NODE_ROLE="${NODE_ROLE:-app}"
NODE_FQDN="${NODE_FQDN:-}"
TIMEZONE="${TIMEZONE:-UTC}"
RUBY_VERSION="${RUBY_VERSION:-3.4.2}"

if ! id -u "${DEPLOY_USER}" >/dev/null 2>&1; then
  echo "[bootstrap] deploy user missing: ${DEPLOY_USER}" >&2
  exit 1
fi

echo "[bootstrap] installing host bootstrap dependencies"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    gnupg2 pass build-essential autoconf bison libssl-dev libyaml-dev \
    libreadline-dev zlib1g-dev libffi-dev libgdbm-dev libncurses5-dev \
    libdb-dev uuid-dev
fi

echo "[bootstrap] configuring deploy user rbenv/ruby (${RUBY_VERSION})"
cat > /etc/profile.d/upright-rbenv.sh <<'EOF_RBENV_PROFILE'
if [ -n "${HOME:-}" ] && [ -d "$HOME/.rbenv" ]; then
  export RBENV_ROOT="$HOME/.rbenv"
  case ":$PATH:" in
    *":$RBENV_ROOT/bin:"*) ;;
    *) export PATH="$RBENV_ROOT/bin:$RBENV_ROOT/shims:$PATH" ;;
  esac
  if command -v rbenv >/dev/null 2>&1; then
    eval "$(rbenv init - bash)" >/dev/null 2>&1 || true
  fi
fi
EOF_RBENV_PROFILE
chmod 644 /etc/profile.d/upright-rbenv.sh

sudo -u "${DEPLOY_USER}" -H bash -lc "
set -euo pipefail
export RBENV_ROOT=\"\$HOME/.rbenv\"
if [[ ! -d \"\$RBENV_ROOT\" ]]; then
  git clone --depth 1 https://github.com/rbenv/rbenv.git \"\$RBENV_ROOT\"
fi
mkdir -p \"\$RBENV_ROOT/plugins\"
if [[ ! -d \"\$RBENV_ROOT/plugins/ruby-build\" ]]; then
  git clone --depth 1 https://github.com/rbenv/ruby-build.git \"\$RBENV_ROOT/plugins/ruby-build\"
fi
if ! grep -q 'upright-rbenv-profile' \"\$HOME/.profile\" 2>/dev/null; then
  cat >> \"\$HOME/.profile\" <<'EOF_RBENV_PROFILE'
# upright-rbenv-profile
export RBENV_ROOT=\"\$HOME/.rbenv\"
export PATH=\"\$RBENV_ROOT/bin:\$RBENV_ROOT/shims:\$PATH\"
if command -v rbenv >/dev/null 2>&1; then
  eval \"\$(rbenv init - bash)\" >/dev/null 2>&1 || true
fi
EOF_RBENV_PROFILE
fi
export PATH=\"\$RBENV_ROOT/bin:\$RBENV_ROOT/shims:\$PATH\"
eval \"\$(rbenv init - bash)\"
rbenv install -s ${RUBY_VERSION}
rbenv global ${RUBY_VERSION}
gem install bundler --no-document
rbenv rehash
"

echo "[bootstrap] configuring hostname/timezone/ssh"
if [[ -n "${NODE_FQDN}" ]]; then
  SHORT_HOST="${NODE_FQDN%%.*}"
  hostnamectl set-hostname "${NODE_FQDN}" || true
  echo "${NODE_FQDN}" > /etc/hostname
  mkdir -p /etc/cloud/cloud.cfg.d
  cat > /etc/cloud/cloud.cfg.d/99_upright_hostname.cfg <<EOF_HOSTCFG
preserve_hostname: true
fqdn: ${NODE_FQDN}
hostname: ${SHORT_HOST}
EOF_HOSTCFG
  if grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
    sed -i "s/^127\\.0\\.1\\.1[[:space:]].*/127.0.1.1 ${NODE_FQDN} ${SHORT_HOST}/" /etc/hosts
  else
    echo "127.0.1.1 ${NODE_FQDN} ${SHORT_HOST}" >> /etc/hosts
  fi
fi

timedatectl set-timezone "${TIMEZONE}" || true

grep -Eq '^Port ' /etc/ssh/sshd_config \
  && sed -i "s/^#\?Port .*/Port ${SSH_PORT}/" /etc/ssh/sshd_config \
  || echo "Port ${SSH_PORT}" >> /etc/ssh/sshd_config

grep -Eq '^PermitRootLogin ' /etc/ssh/sshd_config \
  && sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
  || echo 'PermitRootLogin prohibit-password' >> /etc/ssh/sshd_config

grep -Eq '^PasswordAuthentication ' /etc/ssh/sshd_config \
  && sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config \
  || echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config

echo "${NODE_ROLE}" > /etc/upright-role
chmod 644 /etc/upright-role

echo "[bootstrap] installing /usr/local/bin/upright-remote-pass-bootstrap"
cat > /usr/local/bin/upright-remote-pass-bootstrap <<'REMOTE_BOOTSTRAP'
#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${1:-$HOME/upright}"
PASS_PREFIX="${2:-upright}"
RUBY_VERSION="${3:-3.4.2}"

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -y >/dev/null
  sudo apt-get install -y gnupg2 pass build-essential autoconf bison libssl-dev \
    libyaml-dev libreadline-dev zlib1g-dev libffi-dev libgdbm-dev libncurses5-dev \
    libdb-dev uuid-dev >/dev/null
fi

export RBENV_ROOT="${RBENV_ROOT:-$HOME/.rbenv}"
if [[ ! -d "${RBENV_ROOT}" ]]; then
  git clone --depth 1 https://github.com/rbenv/rbenv.git "${RBENV_ROOT}" >/dev/null 2>&1
fi
mkdir -p "${RBENV_ROOT}/plugins"
if [[ ! -d "${RBENV_ROOT}/plugins/ruby-build" ]]; then
  git clone --depth 1 https://github.com/rbenv/ruby-build.git "${RBENV_ROOT}/plugins/ruby-build" >/dev/null 2>&1
fi
if ! grep -q 'upright-rbenv-profile' "$HOME/.profile" 2>/dev/null; then
  cat >> "$HOME/.profile" <<'EOF_RBENV_PROFILE'
# upright-rbenv-profile
export RBENV_ROOT="$HOME/.rbenv"
export PATH="$RBENV_ROOT/bin:$RBENV_ROOT/shims:$PATH"
if command -v rbenv >/dev/null 2>&1; then
  eval "$(rbenv init - bash)" >/dev/null 2>&1 || true
fi
EOF_RBENV_PROFILE
fi
export PATH="${RBENV_ROOT}/bin:${RBENV_ROOT}/shims:${PATH}"
eval "$(rbenv init - bash)"
rbenv install -s "${RUBY_VERSION}"
rbenv global "${RUBY_VERSION}"
gem install bundler --no-document
rbenv rehash
if ! command -v ruby >/dev/null 2>&1; then
  echo "ruby missing from PATH after rbenv bootstrap" >&2
  exit 1
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
cat > "${REPO_PATH}/bin/load-secrets" <<'EOF_SECRETS'
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
EOF_SECRETS
chmod +x "${REPO_PATH}/bin/load-secrets"
echo "Remote bootstrap ready: REPO_PATH=${REPO_PATH} PASS_PREFIX=${PASS_PREFIX}"
REMOTE_BOOTSTRAP
chmod 0755 /usr/local/bin/upright-remote-pass-bootstrap

systemctl daemon-reload || true
systemctl restart ssh.socket || true
systemctl restart ssh || systemctl restart sshd || true

echo "[bootstrap] complete role=${NODE_ROLE} user=${DEPLOY_USER} ssh_port=${SSH_PORT}"
