# arkham-infra

Consolidated fleet infrastructure covering **Saitama**, **Genos**, **Psykos Host**, and **Psykos WSL2** (Cloudflare configs, OmniRouter tiered routing openrouter.md reference, Headscale mesh routing, Mesh IP Devices.md, and Docker compose manifests across all nodes).

## Directory Structure

| Directory | Purpose |
|---|---|
| `omnirouter/` | OmniRouter tiered routing — gateway scripts, router server, tier reference docs |
| `nginx/` | Nginx reverse proxy configurations |
| `docker-compose/` | Docker Compose manifests for services across all fleet nodes |
| `docs/` | Fleet documentation — Cloudflare, Headscale mesh routing, IP device inventory |
| `deploy/` | Deployment scripts and provisioning helpers |

## Fleet Nodes

- **Saitama** — Primary router / gateway node
- **Genos** — Supporting compute node
- **Psykos Host** — Host machine (shared)
- **Psykos WSL2** — WSL2 development environment (this node)

## Quick Start

```bash
# Clone the repo
git clone git@github.com:bmoney8/arkham-infra.git
cd arkham-infra

# Explore the OmniRouter configs
cat omnirouter/MAKIMA_TIERS.md
```

## License

Internal fleet use.
