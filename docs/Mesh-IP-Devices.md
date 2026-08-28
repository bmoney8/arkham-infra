# Arkham Headscale Mesh Registry
> Source of truth for all Headscale 100.64.0.0/10 node IP allocations, ports, and hosted services.
> Mirrored from: makima-vault/Makima files/Mesh IP Devices.md
> Last synced: 2026-08-27 (V5.5)

## Network Configuration
- Controller Target: 100.64.0.3:8080 (External Bootstrap: 46.62.219.190:8080)
- Mesh Subnet: 100.64.0.0/10
- Active User ID: 1 (bryce)
- Active User ID: 2 (krista)

## Node Registry

| Node | Mesh IP | Platform | Role | Ports |
|------|---------|----------|------|-------|
| Psykos (Host) | 100.64.0.2 | Windows 11 Pro | Primary Dev Workstation / GPU / Native HUD | :8188 ComfyUI, :3000 HUD |
| Saitama | 100.64.0.3 | Hetzner CX33 (Ubuntu) | Sovereign Ingress / Model Routing | :8080 Headscale, :8000 OmniRouter, :7080 Buzz, :8020 Voice, :3000/:3001 Canvas, :8181 Activepieces, :8090 PocketBase, :7437/:80 fsociety |
| Psykos (WSL2) | 100.64.0.4 | Ubuntu 24.04.4 LTS | Central Strategic Daemon / DB Hub | :8010 Dashboard, :7080 Buzz, :3020 Twenty CRM, :8006 Focalboard, :8008 API, :5432 Postgres, :5672/:15672 RabbitMQ, :5678/:5680 n8n, MCP :3000-:3005/:8081-:8084 |
| Genos | 100.64.0.5 | OCI ARM64 Ampere | Knowledge Graph / OSINT Recon | :8000 Mem0, :5432 pgvector, :7474/:7687 Neo4j, :6333/:6334 Qdrant, :8080 MCP, :5678 n8n |
| Do-S | 100.64.0.6 | iOS (iPhone) | Mobile Node / Push Telemetry | — |
| Maiko | 100.64.0.7 | Windows 11 Pro (Dell Lat 5400) | Field Ops / Hermes-HUD | HUD Console |
| Killer Frost | 100.64.0.8 | Windows 11 Home (RTX 5050) | Krista Workstation / EDI Runner | Edge/Intermittent |

## Field Assets

| Asset | Mount | Role |
|-------|-------|------|
| Lexar USB (E:) | /mnt/e or persistence.dat | Portable Kali Runner / light-architect :8002 |
| ADATA USB (F:) | /mnt/f (Ventoy) | Portable Logistics & EDI Runner |
