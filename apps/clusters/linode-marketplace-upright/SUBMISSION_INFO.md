# Akamai Marketplace Submission Info (Draft)

## App Name

Upright Cluster

## App Description (100-125 words)

Upright Cluster deploys a multi-node website monitoring stack on Akamai Cloud with one-click cluster provisioning. The deployment creates a dashboard node and three distributed monitor nodes, configures DNS endpoints, hardens host access, and scaffolds a production-ready Upright application configured for Kamal deployment. Optional HTTPS support can be enabled through a certbot-assisted workflow that stores certificates in a pass-based local secret store. The stack includes built-in Prometheus and Alertmanager accessories and supports recurring HTTP probe scheduling out of the box. The post-deploy helper flow guides operators through secrets setup, application deployment, and runtime probe scheduler validation to reduce first-run failures and speed time-to-observability.

## Version Number

0.1.0 (update for release cut)

## Support URL

https://github.com/mighteejim/upright-setup-scripts/issues

## Operating System

Ubuntu 24.04 LTS

## Included Files for PR

- `README.md`
- `DOCUMENTATION.md`
- StackScript: `deployment_scripts/linode-marketplace-upright/upright-deploy.sh`
- Cluster app tree under `apps/clusters/linode-marketplace-upright`
- Brand assets archive (to be attached separately):
  - Primary color hex
  - Secondary color hex
  - White logo vector
  - Full-color logo vector
