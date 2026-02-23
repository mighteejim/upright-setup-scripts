# Upright Marketplace-Style Cluster

This directory implements a Linode Marketplace-style cluster flow for Upright:

1. StackScript runs on the first node (app/provisioner).
2. `provision.yml` creates monitor nodes (`ord`, `iad`, `sea`) via Linode API.
3. `site.yml` configures all nodes (packages, docker, deploy user, SSH hardening).
4. `site.yml` bootstraps the Upright Rails app on app node by default (`/home/deploy/upright`).
5. `destroy.yml` removes monitor nodes and DNS records created by the playbook.

## Files

- `provision.yml`: create monitor Linodes, wait for SSH, write inventory, optional Linode DNS.
- `site.yml`: configure app + monitor nodes.
- `.ansible-lint`, `.yamllint`: lint configuration for marketplace CI checks.
- `.gitignore`: local Ansible/Python artifact ignores.
- `roles/app_bootstrap`: installs rbenv + Ruby, scaffolds Rails app, installs Upright gem, runs DB setup.
- `roles/app_bootstrap/templates`: renders `config/deploy.yml`, `config/sites.yml`, `.kamal/secrets` using live cluster IP/domain metadata.
- `destroy.yml`: teardown of provisioned monitor resources.
- `group_vars/linode/vars`: runtime variables populated by StackScript.
- `roles/common`: baseline host setup.
- `roles/post`: writes credentials/summary files on app node and installs MOTD guidance.
- `DOCUMENTATION.md`: deployment doc intended for Akamai Marketplace submission package.
- `SUBMISSION_INFO.md`: draft listing metadata (name/description/version/support URL).

## StackScript Entry

Use:

- `/Users/jackley/Developer/oss/upright-setup-scripts/deployment_scripts/linode-marketplace-upright/upright-deploy.sh`

Raw URL pattern for Linode StackScript:

- `https://raw.githubusercontent.com/<org>/<repo>/<branch>/deployment_scripts/linode-marketplace-upright/upright-deploy.sh`
- Set `GIT_REPO` UDF to your public repo URL that contains this cluster tree.
- Optional single HTTP probe UDFs:
  - `HTTP_PROBE_NAME` (default: `Main Website`)
  - `HTTP_PROBE_URL` (blank disables custom probe rendering)
  - `HTTP_PROBE_EXPECTED_STATUS` (default: `200`)
- Optional SSL UDFs:
  - `ENABLE_SSL` (`true|false`, default: `false`)
  - `SSL_REDIRECT` (`true|false`, default: `true`)

## Output Artifacts

On app/provisioner node:

- `/var/log/stackscript.log`
- `/home/<deploy_user>/.credentials`
- `/root/.upright-cluster-info`
- `/home/<deploy_user>/bin/configure-secrets`
- `/home/<deploy_user>/bin/helpers/setup-pass-secrets`
- `/home/<deploy_user>/bin/helpers/setup-certbot-ssl`
- `/home/<deploy_user>/bin/helpers/load-secrets`
- `/home/<deploy_user>/bin/helpers/verify-probe-scheduler`
- `/home/<deploy_user>/upright/config/deploy.yml`
- `/home/<deploy_user>/upright/config/sites.yml`
- `/home/<deploy_user>/upright/.kamal/secrets`

## Scope

This flow handles infrastructure + baseline host configuration + app scaffolding.
Kamal deploy remains an operator step after secrets are loaded.

## Certbot SSL Path

When `ENABLE_SSL=true`:

1. Run `eval "$(~/bin/configure-secrets)"`
   - This runs: `setup-pass-secrets` -> `setup-certbot-ssl` -> `load-secrets`
2. Run `cd ~/upright && bin/kamal setup && bin/kamal deploy`
3. Run `~/bin/verify-probe-scheduler`

## Linode Interfaces Note

Linode node creation now sends an explicit primary public interface by default (`use_linode_interfaces: true`).
`private_ip` is disabled by default (`enable_private_ipv4: false`) to support Linode Interfaces.
If your account/regions support legacy private IP behavior, you can set `enable_private_ipv4: true`.
