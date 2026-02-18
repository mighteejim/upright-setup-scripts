#!/usr/bin/env bash
# <UDF name="DEPLOY_USER" label="Deploy User" default="deploy" />
# <UDF name="DEPLOY_PASSWORD" label="Deploy User Password (optional)" default="" />
# <UDF name="DEPLOY_SSH_PUBKEY" label="Deploy User SSH Public Key" />
# <UDF name="SSH_PORT" label="SSH Port" default="2222" />
# <UDF name="NODE_ROLE" label="Node Role (app|ord|iad|sea)" default="app" />
# <UDF name="NODE_FQDN" label="Node FQDN" default="" />
# <UDF name="ENABLE_FAIL2BAN" label="Enable fail2ban (yes|no)" default="yes" />
# <UDF name="TIMEZONE" label="Timezone" default="UTC" />
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_PASSWORD="${DEPLOY_PASSWORD:-}"
DEPLOY_SSH_PUBKEY="${DEPLOY_SSH_PUBKEY:-}"
SSH_PORT="${SSH_PORT:-2222}"
NODE_ROLE="${NODE_ROLE:-app}"
NODE_FQDN="${NODE_FQDN:-}"
ENABLE_FAIL2BAN="${ENABLE_FAIL2BAN:-yes}"
TIMEZONE="${TIMEZONE:-UTC}"

if [[ -z "${DEPLOY_SSH_PUBKEY}" ]]; then
  echo "DEPLOY_SSH_PUBKEY is required" >&2
  exit 1
fi

if ! id -u "${DEPLOY_USER}" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "${DEPLOY_USER}"
fi

if [[ -n "${DEPLOY_PASSWORD}" ]]; then
  echo "${DEPLOY_USER}:${DEPLOY_PASSWORD}" | chpasswd
fi

install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh"
AUTH_KEYS="/home/${DEPLOY_USER}/.ssh/authorized_keys"
touch "${AUTH_KEYS}"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "${AUTH_KEYS}"
chmod 600 "${AUTH_KEYS}"
if ! grep -Fqx "${DEPLOY_SSH_PUBKEY}" "${AUTH_KEYS}"; then
  echo "${DEPLOY_SSH_PUBKEY}" >> "${AUTH_KEYS}"
fi

cat > "/etc/sudoers.d/90-${DEPLOY_USER}" <<SUDOERS
${DEPLOY_USER} ALL=(ALL) NOPASSWD:ALL
SUDOERS
chmod 440 "/etc/sudoers.d/90-${DEPLOY_USER}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y \
  ca-certificates curl gnupg gnupg2 jq git pass ufw unzip tree software-properties-common

if [[ "${ENABLE_FAIL2BAN}" == "yes" ]]; then
  apt-get install -y fail2ban
  systemctl enable --now fail2ban || true
fi

install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi

ARCH="$(dpkg --print-architecture)"
CODENAME="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
cat > /etc/apt/sources.list.d/docker.list <<DOCKERLIST
deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable
DOCKERLIST

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "${DEPLOY_USER}"

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

cat > /usr/local/bin/upright-remote-pass-bootstrap <<'REMOTE_BOOTSTRAP'
#!/usr/bin/env bash
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

echo "Bootstrap complete for role=${NODE_ROLE} user=${DEPLOY_USER} ssh_port=${SSH_PORT}"
