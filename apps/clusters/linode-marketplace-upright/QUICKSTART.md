# Upright StackScript Quickstart

## 1) Create the app node with this StackScript

Use raw script URL:

`https://raw.githubusercontent.com/mighteejim/upright-setup-scripts/main/deployment_scripts/linode-marketplace-upright/upright-deploy.sh`

## 2) Fill UDF fields

Required:

- `TOKEN_PASSWORD`: Linode API token with write access to Linodes + Domains.
- `DEPLOY_USER`: Linux deploy user, usually `deploy`.
- `DEPLOY_SSH_PUBKEY`: your local SSH public key (example: `cat ~/.ssh/id_ed25519.pub`).
- `ROOT_DOMAIN`: base domain, example `heydayops.com` (required for `linode-dns` mode).
- `UPRIGHT_SUFFIX_LABEL`: subdomain prefix, default `up`.
- `DNS_MODE`: `linode-dns` (recommended) or `manual`.
- `GIT_REPO`: repo URL containing this cluster app, example `https://github.com/mighteejim/upright-setup-scripts`.
- `GIT_BRANCH`: branch to deploy from, example `main`.
- `REGISTRY_USERNAME`: GitHub username only, example `mighteejim` (not full `ghcr.io/...` image).

Optional:

- `HTTP_PROBE_NAME`, `HTTP_PROBE_URL`, `HTTP_PROBE_EXPECTED_STATUS`
- `ENABLE_SSL`, `SSL_REDIRECT`

## 3) Wait for StackScript completion

From your laptop:

```bash
ssh deploy@app.<suffix>.<root_domain> 'tail -f /var/log/stackscript.log'
```

## 4) Configure secrets + deploy

On app node:

```bash
eval "$(~/bin/configure-secrets)"
cd ~/upright
bin/kamal setup
bin/kamal deploy
~/bin/verify-probe-scheduler
```

## 5) GitHub token for GHCR (used as `KAMAL_REGISTRY_PASSWORD`)

Create a GitHub token for your registry account:

1. GitHub -> Settings -> Developer settings -> Personal access tokens.
2. Fine-grained token (preferred), repo access to image repo, permission:
   - `Packages: Read`
3. If image is private and fine-grained access is blocked in your org, use classic PAT with:
   - `read:packages`
   - `repo` (only when needed for private package visibility)
4. During `setup-pass-secrets`, paste this token for `KAMAL_REGISTRY_PASSWORD`.

Sanity check:

```bash
printf '%s' "$KAMAL_REGISTRY_PASSWORD" | docker login ghcr.io -u <github-username> --password-stdin
```
