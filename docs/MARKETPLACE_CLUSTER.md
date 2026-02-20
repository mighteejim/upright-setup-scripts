# Marketplace Cluster Path (No Python Wizard)

This repo now includes a standalone Linode Marketplace-style deployment path.

## Goal

Reduce failure points by moving orchestration to:

- thin StackScript bootstrap
- Ansible `provision.yml` + `site.yml` + `destroy.yml`

and stop coupling deployment lifecycle to `bin/upright-linode-setup.py`.

## Layout

- `deployment_scripts/linode-marketplace-upright/upright-deploy.sh`
- `apps/clusters/linode-marketplace-upright/`
  - `provision.yml`
  - `site.yml`
  - `destroy.yml`
  - `roles/common`
  - `roles/post`

## Run Model

1. Launch first Linode (app/provisioner) with `upright-deploy.sh` StackScript.
   Set `GIT_REPO` to the public repository URL containing these files.
2. Script clones this repo, installs ansible runtime, writes vars, runs playbooks.
3. Ansible creates monitor nodes and configures all nodes.
4. Operator completes app-level Rails/Kamal steps.

## Notes

- Current flow defaults to Ubuntu 24.04 (`linode/ubuntu24.04`).
- DNS supports `linode-dns` and `manual` modes.
- Destroy playbook removes monitor nodes created by this flow.
