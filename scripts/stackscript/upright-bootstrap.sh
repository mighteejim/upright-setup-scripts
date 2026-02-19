#!/usr/bin/env bash
# <UDF name="DEPLOY_USER" label="Deploy User" default="deploy" />
# <UDF name="DEPLOY_PASSWORD" label="Deploy User Password (optional)" default="" />
# <UDF name="DEPLOY_SSH_PUBKEY" label="Deploy User SSH Public Key" />
# <UDF name="SSH_PORT" label="SSH Port" default="2222" />
# <UDF name="NODE_ROLE" label="Node Role (app|ord|iad|sea)" default="app" />
# <UDF name="NODE_FQDN" label="Node FQDN" default="" />
# <UDF name="ENABLE_FAIL2BAN" label="Enable fail2ban (yes|no)" default="yes" />
# <UDF name="TIMEZONE" label="Timezone" default="UTC" />
# <UDF name="RUBY_VERSION" label="Ruby Version" default="3.4.2" />
# <UDF name="BOOTSTRAP_REPO" label="Bootstrap Repo (owner/repo)" default="mighteejim/upright-setup-scripts" />
# <UDF name="BOOTSTRAP_REF" label="Bootstrap Ref (tag/sha)" default="main" />
# <UDF name="BOOTSTRAP_PATH" label="Bootstrap Script Path" default="scripts/bootstrap/upright-host-bootstrap.sh" />
# <UDF name="BOOTSTRAP_SHA256" label="Bootstrap Script SHA256 (optional)" default="" />
# <UDF name="BOOTSTRAP_URL" label="Bootstrap Raw URL Override (optional)" default="" />
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_PASSWORD="${DEPLOY_PASSWORD:-}"
DEPLOY_SSH_PUBKEY="${DEPLOY_SSH_PUBKEY:-}"
SSH_PORT="${SSH_PORT:-2222}"
NODE_ROLE="${NODE_ROLE:-app}"
NODE_FQDN="${NODE_FQDN:-}"
ENABLE_FAIL2BAN="${ENABLE_FAIL2BAN:-yes}"
TIMEZONE="${TIMEZONE:-UTC}"
RUBY_VERSION="${RUBY_VERSION:-3.4.2}"
BOOTSTRAP_REPO="${BOOTSTRAP_REPO:-mighteejim/upright-setup-scripts}"
BOOTSTRAP_REF="${BOOTSTRAP_REF:-main}"
BOOTSTRAP_PATH="${BOOTSTRAP_PATH:-scripts/bootstrap/upright-host-bootstrap.sh}"
BOOTSTRAP_SHA256="${BOOTSTRAP_SHA256:-}"
BOOTSTRAP_URL="${BOOTSTRAP_URL:-}"

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
apt-get install -y ca-certificates curl gnupg jq git ufw unzip tree software-properties-common

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

if [[ -z "${BOOTSTRAP_URL}" ]]; then
  BOOTSTRAP_URL="https://raw.githubusercontent.com/${BOOTSTRAP_REPO}/${BOOTSTRAP_REF}/${BOOTSTRAP_PATH}"
fi

TMP_BOOTSTRAP="/tmp/upright-host-bootstrap.sh"
echo "Fetching remote bootstrap: ${BOOTSTRAP_URL}"
curl -fsSL "${BOOTSTRAP_URL}" -o "${TMP_BOOTSTRAP}"

if [[ -n "${BOOTSTRAP_SHA256}" ]]; then
  echo "${BOOTSTRAP_SHA256}  ${TMP_BOOTSTRAP}" | sha256sum -c -
fi

chmod 0755 "${TMP_BOOTSTRAP}"
export DEPLOY_USER DEPLOY_PASSWORD DEPLOY_SSH_PUBKEY SSH_PORT NODE_ROLE NODE_FQDN ENABLE_FAIL2BAN TIMEZONE RUBY_VERSION
"${TMP_BOOTSTRAP}"

echo "StackScript complete for role=${NODE_ROLE} user=${DEPLOY_USER} ssh_port=${SSH_PORT}"
