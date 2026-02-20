# Upright Marketplace-Style Cluster

This directory implements a Linode Marketplace-style cluster flow for Upright:

1. StackScript runs on the first node (app/provisioner).
2. `provision.yml` creates monitor nodes (`ord`, `iad`, `sea`) via Linode API.
3. `site.yml` configures all nodes (packages, docker, deploy user, SSH hardening).
4. `destroy.yml` removes monitor nodes and DNS records created by the playbook.

## Files

- `provision.yml`: create monitor Linodes, wait for SSH, write inventory, optional Linode DNS.
- `site.yml`: configure app + monitor nodes.
- `destroy.yml`: teardown of provisioned monitor resources.
- `group_vars/linode/vars`: runtime variables populated by StackScript.
- `roles/common`: baseline host setup.
- `roles/post`: writes credentials/summary files on app node.

## StackScript Entry

Use:

- `/Users/jackley/Developer/oss/upright-setup-scripts/deployment_scripts/linode-marketplace-upright/upright-deploy.sh`

Raw URL pattern for Linode StackScript:

- `https://raw.githubusercontent.com/<org>/<repo>/<branch>/deployment_scripts/linode-marketplace-upright/upright-deploy.sh`
- Set `GIT_REPO` UDF to your public repo URL that contains this cluster tree.

## Output Artifacts

On app/provisioner node:

- `/var/log/stackscript.log`
- `/home/<deploy_user>/.credentials`
- `/root/.upright-cluster-info`
- `/home/<deploy_user>/bin/setup-pass-secrets`
- `/home/<deploy_user>/bin/load-secrets`

## Scope

This flow handles infrastructure + baseline host configuration.
Rails app scaffolding and Kamal deploy are intentionally left as a follow-up operator step.

## Linode Interfaces Note

`private_ip` is disabled by default (`enable_private_ipv4: false`) to support Linode Interfaces.
If your account/regions support legacy private IP behavior, you can set `enable_private_ipv4: true`.
