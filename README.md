# Upright Deployment (up.ckley.net)

Production deployment notes for the Upright multi-site rollout on Ubuntu 22.04 with Kamal.

## Topology

- `app.up.ckley.net`: admin/dashboard web node
- `ord.up.ckley.net`, `iad.up.ckley.net`, `sea.up.ckley.net`: probe sites
- `jobs` role runs probe scheduling/execution on probe nodes
- `playwright` accessory is required for Playwright probes

## Important Deployment Behavior

- In this setup, Upright site links are derived from `SITE_SUBDOMAIN` + `UPRIGHT_HOSTNAME`.
- Multi-web deploys cannot use Kamal built-in TLS directly without custom cert wiring.
- TLS is terminated externally; Kamal proxy runs HTTP internally.

## Required Config Highlights

`config/deploy.yml` should include:

- `UPRIGHT_HOSTNAME: up.ckley.net`
- per-tag `SITE_SUBDOMAIN` values (`app`, `ord`, `iad`, `sea`)
- `PLAYWRIGHT_SERVER_URL: ws://upright-playwright:53333/playwright`

The `/playwright` suffix is required for successful WebSocket handshake.

## Docs

- Deployment runbook: `docs/DEPLOY.md`
- Troubleshooting log: `docs/TROUBLESHOOTING.md`

## Quick Preflight

1. All required secrets resolve (registry + Rails/admin/probe creds).
2. SSH and passwordless sudo are working on all target hosts.
3. Docker is usable by deploy user on deploy and target hosts.
4. Host keys in `known_hosts` match current server fingerprints.
5. Playwright accessory is running on jobs hosts.
6. Sufficient disk space exists on all nodes for image pull/extract.

## Deploy

```bash
bin/kamal setup
bin/kamal deploy
```

If setup/deploy fails, start with `docs/TROUBLESHOOTING.md`.
