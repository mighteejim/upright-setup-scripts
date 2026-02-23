# Upright Cluster Deployment Documentation

## Required Distribution

- Ubuntu 24.04 LTS

## Suggested Plan

- App/provisioner node: `g6-standard-2` (or higher)
- Monitor nodes (`ord`, `iad`, `sea`): `g6-standard-2` (or higher)
- Recommended minimum cluster: 4 nodes total (1 app + 3 monitors)

## Limited User Fields

This deployment requires a non-root sudo user (`DEPLOY_USER`) and SSH public key (`DEPLOY_SSH_PUBKEY`).  
Provisioned nodes are hardened to use key-based auth for the deploy user, with root login disabled by role configuration.

Reference shortguide:  
https://github.com/linode/docs/blob/develop/docs/products/tools/marketplace/_shortguides/marketplace-required-limited-user-fields-shortguide/index.md

## Required UDF Inputs

- `TOKEN_PASSWORD` (Linode API token)
- `DEPLOY_USER`
- `DEPLOY_SSH_PUBKEY`
- `ROOT_DOMAIN` (required when `DNS_MODE=linode-dns`)
- `UPRIGHT_SUFFIX_LABEL` (default `up`)
- `DNS_MODE` (`linode-dns` or `manual`)
- `DNS_TTL_SEC` (default `120`)
- `NODE_TYPE` (default `g6-standard-2`)
- `IMAGE` (default `linode/ubuntu24.04`)
- `ORD_REGION`, `IAD_REGION`, `SEA_REGION`
- `CLUSTER_NAME` (default `upright`)
- `REGISTRY_USERNAME` (GitHub username for `ghcr.io/<username>/upright`)
- Optional probe fields: `HTTP_PROBE_NAME`, `HTTP_PROBE_URL`, `HTTP_PROBE_EXPECTED_STATUS`
- Optional SSL fields: `ENABLE_SSL`, `SSL_REDIRECT`
- `GIT_REPO` and optional `GIT_BRANCH`

## Provisioning and Post-Deploy Flow

1. StackScript boots app/provisioner node and installs Ansible runtime.
2. `provision.yml` creates monitor nodes and optional Linode DNS records.
3. `site.yml` configures all nodes (Docker, security baseline, deploy user, SSH).
4. App bootstrap generates `config/deploy.yml`, `config/sites.yml`, `.kamal/secrets`, `probes/http_probes.yml`, and recurring probe scheduler config.
5. Post role writes credentials and helper scripts.

Run on app node:

```bash
eval "$(~/bin/configure-secrets)"
cd ~/upright
bin/kamal setup
bin/kamal deploy
~/bin/verify-probe-scheduler
```

## Credential Locations

- `/home/<deploy_user>/.credentials` (deployment summary and endpoints)
- `/root/.upright-cluster-info` (cluster metadata)
- pass-based deploy secrets via helpers:
  - `~/bin/helpers/setup-pass-secrets`
  - `~/bin/helpers/load-secrets`
  - optional SSL helper: `~/bin/helpers/setup-certbot-ssl`

## Accessing the App

- Dashboard: `app.<suffix>.<root_domain>` (or app node public IP in manual DNS mode)
- Monitor endpoints:
  - `ord.<suffix>.<root_domain>`
  - `iad.<suffix>.<root_domain>`
  - `sea.<suffix>.<root_domain>`

## Screenshots

Add screenshots in the submission PR:

1. Cloud Manager StackScript UDF form populated for deployment
2. Successful dashboard login page
3. Upright dashboard after initial login
