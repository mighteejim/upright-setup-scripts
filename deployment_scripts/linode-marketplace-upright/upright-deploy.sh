#!/usr/bin/env bash

# enable logs in Lish + local file
exec > >(tee /dev/ttyS0 /var/log/stackscript.log) 2>&1

set -euo pipefail

# <UDF name="TOKEN_PASSWORD" label="Linode API token" />
# <UDF name="DEPLOY_USER" label="Deploy user" default="deploy" />
# <UDF name="DEPLOY_SSH_PUBKEY" label="Deploy SSH public key" />
# <UDF name="ROOT_DOMAIN" label="Root domain" example="example.com" default="" />
# <UDF name="UPRIGHT_SUFFIX_LABEL" label="Upright suffix label" example="up" default="up" />
# <UDF name="DNS_MODE" label="DNS mode" oneOf="linode-dns,manual" default="linode-dns" />
# <UDF name="DNS_TTL_SEC" label="DNS TTL seconds" default="120" />
# <UDF name="NODE_TYPE" label="Linode plan" default="g6-standard-2" />
# <UDF name="IMAGE" label="Image slug" default="linode/ubuntu24.04" />
# <UDF name="ORD_REGION" label="ORD region" default="us-ord" />
# <UDF name="IAD_REGION" label="IAD region" default="us-iad" />
# <UDF name="SEA_REGION" label="SEA region" default="us-sea" />
# <UDF name="CLUSTER_NAME" label="Cluster name prefix" default="upright" />
# <UDF name="TIMEZONE" label="Timezone" default="UTC" />
# <UDF name="KAMAL_SSH_PORT" label="Kamal SSH port" default="22" />
# <UDF name="REGISTRY_USERNAME" label="Container registry username (GitHub username)" default="your-github-username" />
# <UDF name="GIT_REPO" label="Git repo URL" default="" />
# <UDF name="GIT_BRANCH" label="Git branch" default="main" />

TOKEN_PASSWORD="${TOKEN_PASSWORD:-}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_SSH_PUBKEY="${DEPLOY_SSH_PUBKEY:-}"
ROOT_DOMAIN="${ROOT_DOMAIN:-}"
UPRIGHT_SUFFIX_LABEL="${UPRIGHT_SUFFIX_LABEL:-up}"
DNS_MODE="${DNS_MODE:-linode-dns}"
DNS_TTL_SEC="${DNS_TTL_SEC:-120}"
NODE_TYPE="${NODE_TYPE:-g6-standard-2}"
IMAGE="${IMAGE:-linode/ubuntu24.04}"
ORD_REGION="${ORD_REGION:-us-ord}"
IAD_REGION="${IAD_REGION:-us-iad}"
SEA_REGION="${SEA_REGION:-us-sea}"
CLUSTER_NAME="${CLUSTER_NAME:-upright}"
TIMEZONE="${TIMEZONE:-UTC}"
KAMAL_SSH_PORT="${KAMAL_SSH_PORT:-22}"
REGISTRY_SERVER="ghcr.io"
REGISTRY_USERNAME="${REGISTRY_USERNAME:-your-github-username}"
AUTO_DESTROY_ON_FAILURE="true"
UPRIGHT_BOOTSTRAP_APP="true"
UPRIGHT_APP_PATH="/home/deploy/upright"
UPRIGHT_RUBY_VERSION="3.4.2"
UPRIGHT_RAILS_VERSION="8.1.2"
GIT_REPO="${GIT_REPO:-}"
GIT_BRANCH="${GIT_BRANCH:-main}"

WORK_DIR="/tmp/upright-marketplace"
APP_DIR="${WORK_DIR}/apps/clusters/linode-marketplace-upright"
ANSIBLE_KEY_PATH="${HOME}/.ssh/id_ansible_ed25519"
ANSIBLE_LOG_PATH="${ANSIBLE_LOG_PATH:-/var/log/ansible-upright.log}"

if [[ -z "${TOKEN_PASSWORD}" ]]; then
  echo "TOKEN_PASSWORD is required" >&2
  exit 1
fi
if [[ -z "${GIT_REPO}" ]]; then
  echo "GIT_REPO is required" >&2
  exit 1
fi
if [[ -z "${DEPLOY_SSH_PUBKEY}" ]]; then
  echo "DEPLOY_SSH_PUBKEY is required" >&2
  exit 1
fi
# Normalize REGISTRY_USERNAME so accidental full-image input still works.
if [[ "${REGISTRY_USERNAME}" == https://* ]]; then
  REGISTRY_USERNAME="${REGISTRY_USERNAME#https://}"
elif [[ "${REGISTRY_USERNAME}" == http://* ]]; then
  REGISTRY_USERNAME="${REGISTRY_USERNAME#http://}"
fi
if [[ "${REGISTRY_USERNAME}" == "${REGISTRY_SERVER}/"* ]]; then
  REGISTRY_USERNAME="${REGISTRY_USERNAME#${REGISTRY_SERVER}/}"
fi
if [[ "${REGISTRY_USERNAME}" == */* ]]; then
  REGISTRY_USERNAME="${REGISTRY_USERNAME%%/*}"
fi
if [[ -z "${REGISTRY_USERNAME}" || "${REGISTRY_USERNAME}" == "your-github-username" ]]; then
  echo "REGISTRY_USERNAME is required and cannot be placeholder 'your-github-username'" >&2
  exit 1
fi
IMAGE_NAME="${REGISTRY_USERNAME}/upright"
if [[ "${DNS_MODE}" == "linode-dns" && -z "${ROOT_DOMAIN}" ]]; then
  echo "ROOT_DOMAIN is required when DNS_MODE=linode-dns" >&2
  exit 1
fi
if [[ "${DNS_MODE}" != "linode-dns" && "${DNS_MODE}" != "manual" ]]; then
  echo "DNS_MODE must be one of: linode-dns, manual" >&2
  exit 1
fi
if ! [[ "${KAMAL_SSH_PORT}" =~ ^[0-9]+$ ]]; then
  echo "KAMAL_SSH_PORT must be numeric" >&2
  exit 1
fi
if ! [[ "${DNS_TTL_SEC}" =~ ^[0-9]+$ ]]; then
  echo "DNS_TTL_SEC must be numeric" >&2
  exit 1
fi

run_retry() {
  local attempts="$1"
  local delay_s="$2"
  shift 2
  local n=1
  while true; do
    if "$@"; then
      return 0
    fi
    if [[ "${n}" -ge "${attempts}" ]]; then
      return 1
    fi
    echo "retry ${n}/${attempts} failed; sleeping ${delay_s}s: $*"
    sleep "${delay_s}"
    n=$((n + 1))
  done
}

cleanup() {
  local status="$1"
  local line="$2"
  if [[ "$status" -ne 0 ]]; then
    echo "Deployment failed at line ${line}; stage=${DEPLOY_STAGE}"
    if [[ "${AUTO_DESTROY_ON_FAILURE}" == "true" && "${DEPLOY_STAGE}" == "provision" ]]; then
      echo "Auto-destroy enabled for provision-stage failure; attempting destroy playbook"
      if [[ -f "${APP_DIR}/destroy.yml" ]]; then
        (
          cd "${APP_DIR}"
          source env/bin/activate || true
          ansible-playbook -v destroy.yml || true
        )
      fi
    else
      echo "Skipping auto-destroy (AUTO_DESTROY_ON_FAILURE=${AUTO_DESTROY_ON_FAILURE}, stage=${DEPLOY_STAGE})"
    fi
  fi

  if [[ -f "${ANSIBLE_KEY_PATH}" ]]; then
    rm -f "${ANSIBLE_KEY_PATH}" "${ANSIBLE_KEY_PATH}.pub"
  fi

  if [[ "$status" -eq 0 ]]; then
    rm -rf "${WORK_DIR}"
  else
    echo "Debug artifacts kept at ${WORK_DIR}"
  fi
}
trap 'cleanup $? $LINENO' EXIT
DEPLOY_STAGE="bootstrap"

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none
touch "${ANSIBLE_LOG_PATH}" && chmod 0644 "${ANSIBLE_LOG_PATH}"
export ANSIBLE_LOG_PATH
echo "[stage] install bootstrap dependencies"
run_retry 10 8 apt-get update && apt-get upgrade -y
run_retry 10 8 apt-get install -y git jq curl python3 python3-venv python3-pip ca-certificates

rm -rf "${WORK_DIR}"
echo "[stage] clone repo ${GIT_REPO}@${GIT_BRANCH}"
run_retry 5 5 git clone --depth 1 --branch "${GIT_BRANCH}" "${GIT_REPO}" "${WORK_DIR}"

if [[ ! -d "${APP_DIR}" ]]; then
  echo "Missing app dir: ${APP_DIR}" >&2
  exit 1
fi

python3 -m venv "${APP_DIR}/env"
source "${APP_DIR}/env/bin/activate"
echo "[stage] install ansible runtime"
run_retry 5 5 pip install --upgrade pip
run_retry 5 8 pip install -r "${APP_DIR}/requirements.txt"
run_retry 5 8 ansible-galaxy collection install -r "${APP_DIR}/collections.yml"

install -d -m 700 "${HOME}/.ssh"
ssh-keygen -t ed25519 -N "" -f "${ANSIBLE_KEY_PATH}" >/dev/null
PROVISIONER_SSH_PUB_KEY="$(cat "${ANSIBLE_KEY_PATH}.pub")"

declare APP_LABEL APP_PUBLIC_IPV4
APP_LABEL="$(curl -fsSL -H "Authorization: Bearer ${TOKEN_PASSWORD}" \
  "https://api.linode.com/v4/linode/instances/${LINODE_ID}" | jq -r '.label')"
APP_PUBLIC_IPV4="$(curl -fsSL -H "Authorization: Bearer ${TOKEN_PASSWORD}" \
  "https://api.linode.com/v4/linode/instances/${LINODE_ID}" | jq -r '.ipv4[0]')"

if [[ -z "${APP_PUBLIC_IPV4}" || "${APP_PUBLIC_IPV4}" == "null" ]]; then
  echo "Failed to resolve provisioner public IPv4" >&2
  exit 1
fi

CLUSTER_UUID="$(uuidgen | tr '[:upper:]' '[:lower:]' | cut -d- -f1)"

cat > "${APP_DIR}/group_vars/linode/vars" <<VARS
cluster_name: "${CLUSTER_NAME}"
cluster_uuid: "${CLUSTER_UUID}"
node_type: "${NODE_TYPE}"
image: "${IMAGE}"
ord_region: "${ORD_REGION}"
iad_region: "${IAD_REGION}"
sea_region: "${SEA_REGION}"
use_linode_interfaces: true
enable_private_ipv4: false
bootstrap_ssh_port: 22

token_password: "${TOKEN_PASSWORD}"
provisioner_ssh_pubkey: "${PROVISIONER_SSH_PUB_KEY}"
deploy_user: "${DEPLOY_USER}"
deploy_ssh_pubkey: "${DEPLOY_SSH_PUBKEY}"
timezone: "${TIMEZONE}"
kamal_ssh_port: ${KAMAL_SSH_PORT}
registry_server: "${REGISTRY_SERVER}"
registry_username: "${REGISTRY_USERNAME}"
image_name: "${IMAGE_NAME}"

dns_mode: "${DNS_MODE}"
root_domain: "${ROOT_DOMAIN}"
upright_suffix_label: "${UPRIGHT_SUFFIX_LABEL}"
dns_ttl_sec: ${DNS_TTL_SEC}

app_label: "${APP_LABEL}"
app_public_ipv4: "${APP_PUBLIC_IPV4}"
upright_bootstrap_app: ${UPRIGHT_BOOTSTRAP_APP}
upright_app_path: "${UPRIGHT_APP_PATH}"
upright_ruby_version: "${UPRIGHT_RUBY_VERSION}"
upright_rails_version: "${UPRIGHT_RAILS_VERSION}"
VARS

cat > "${APP_DIR}/ansible.cfg" <<CFG
[ssh_connection]
retries = 5

[defaults]
host_key_checking = False
deprecation_warnings = False
roles_path = ./roles
private_key_file = ${ANSIBLE_KEY_PATH}
CFG

cd "${APP_DIR}"
echo "[stage] run provision playbook"
DEPLOY_STAGE="provision"
run_retry 3 10 ansible-playbook -v provision.yml
echo "[stage] run site playbook"
DEPLOY_STAGE="site"
run_retry 3 10 ansible-playbook -v -i hosts site.yml
DEPLOY_STAGE="complete"

echo "Upright cluster playbooks finished"
echo "- stackscript log: /var/log/stackscript.log"
echo "- ansible log: ${ANSIBLE_LOG_PATH}"
echo "- credentials: /home/${DEPLOY_USER}/.credentials"
echo "- cluster summary: /root/.upright-cluster-info"
