# Upright Linode Setup Wizard

This guide covers end-to-end usage of `bin/upright-linode-setup`.

## What It Does

The wizard is intended to run locally from the repo root and automates:

1. Linode bootstrap StackScript sync (`scripts/stackscript/upright-bootstrap.sh`)
2. Provisioning `app`, `ord`, `iad`, `sea` Linodes
3. DNS setup (`linode-dns`, `cloudflare-dns`, or `manual`) + propagation checks
4. Config generation:
   - `config/deploy.yml`
   - `config/sites.yml`
   - `.kamal/secrets`
5. Optional `kamal setup` + `kamal deploy`
6. Post-deploy HTTPS endpoint checks

## Prerequisites

Run from repo root:

```bash
cd ~/app.up.ckley.net
```

The script checks for:

- `linode-cli` (auto-install attempted if missing)
- `jq`
- `ssh`
- `dig`
- `docker`
- `openssl`
- `curl`
- `bin/kamal`
- `bin/load-secrets`

## Command Reference

### Standard run

```bash
bin/upright-linode-setup
```

### Agent / CI run (non-interactive)

```bash
bin/upright-linode-setup \
  --dry-run \
  --non-interactive \
  --yes \
  --root-domain ckley.net \
  --dns-mode manual \
  --manual-dns-confirmed \
  --skip-deploy \
  --output-json /tmp/upright-agent-output.json
```

### Agent wrapper (recommended)

```bash
bin/upright-linode-setup-agent \
  --dry-run \
  --root-domain ckley.net
```

Wrapper defaults:

- `--non-interactive`
- `--yes`
- `--skip-deploy`
- `--dns-mode manual --manual-dns-confirmed` (unless `--dns-mode` is provided)
- `--output-json /tmp/upright-agent-output.json` (unless `--output-json` is provided)

Notes:

- `--non-interactive` disables prompts and uses flags/defaults only.
- For SSH keys, use one of:
  - `--ssh-key-source linode-all`
  - `--ssh-key-source linode-ids --ssh-key-ids "123,456"`
  - `--ssh-key-source local-path --ssh-pubkey-path ~/.ssh/id_ed25519.pub`
- `--output-json` includes selected config, phase, and current `infra/state.json` snapshot.

### Dry run

```bash
bin/upright-linode-setup --dry-run
```

### Resume

```bash
bin/upright-linode-setup --resume
```

### Destroy

```bash
bin/upright-linode-setup --destroy
```

## Standard Flow

`bin/upright-linode-setup` prompts for:

1. Linode PAT
2. SSH public key path
3. Root domain and upright suffix
   - if you pick a domain from your Linode domain list, DNS mode auto-switches to `linode-dns`
4. Plan mode:
   - Recommended (default): 4 nodes on `g6-standard-2` (Shared 4 GB)
   - Manual: pick any allowed shared/dedicated plan
5. Provisioning mode:
   - `classic` (default): sequential shell logs
6. Instance image
5. Deploy user and SSH port
6. Registry server/username/image name
7. DNS mode
8. Explicit DC/region for each node:
   - `app`
   - `ord`
   - `iad`
   - `sea`
   - default recommendation:
     - `app`: `us-iad`
     - `ord`: `us-ord`
     - `iad`: `us-iad`
     - `sea`: `us-sea`
   - press Enter to accept each default
9. Confirmation with deployment table:
   - node hostname
   - selected region
   - selected plan
   - approximate monthly cost per node + total

Then it will:

1. Create/update private StackScript
2. Provision Linodes and wait for `running` + IPv4
3. Configure DNS and verify via `dig`
4. Generate config files
5. Ask whether to run deploy immediately

If deploy is skipped, run:

```bash
eval "$(bin/load-secrets)"
bin/kamal setup
bin/kamal deploy
```

## DNS Modes

### `linode-dns`

- Uses Linode Domains API via `linode-cli`.
- Requires domain zone to exist in Linode DNS.

### `cloudflare-dns`

- Uses Cloudflare API token.
- Token needs at least:
  - Zone DNS Edit
  - Zone Read

### `manual`

- Wizard prints required A records.
- You create records in your DNS provider.
- Wizard waits for confirmation and then verifies resolution.

## State File

Wizard state is saved to:

- `infra/state.json`

It includes:

- current phase
- stackscript id
- config inputs
- node metadata (role, region, Linode ID, IPv4)
- DNS mode/status

## Resume Behavior

`--resume` continues based on `phase` in `infra/state.json`.

Examples:

- `planning/provisioning` -> continue provisioning, DNS, config, deploy/checks
- `provisioned` -> continue DNS, config, deploy/checks
- `dns_configured` -> continue config, deploy/checks
- `deployed` -> run post checks only

## Destroy Behavior

`--destroy` performs:

1. PAT validation
2. typed confirmation (`DESTROY`)
3. DNS deletion for managed modes (`linode-dns` / `cloudflare-dns`)
4. Linode deletion using IDs from state
5. optional StackScript deletion
6. state archive to `infra/state.destroyed.<timestamp>.json`

For `manual` DNS mode, DNS deletion is skipped.

## Security Notes

- PAT and Cloudflare token are never written to disk.
- Secrets remain env-backed through `.kamal/secrets`.
- Use `pass` + `bin/load-secrets` for deploy secret loading.

## Recommended First Test

Use dry-run first:

```bash
bin/upright-linode-setup --dry-run
```

Then run full setup:

```bash
bin/upright-linode-setup
```
