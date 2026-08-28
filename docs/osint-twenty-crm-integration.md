# OSINT Recon → Twenty CRM Integration
> Phase 4: Point OSINT recon pipelines to Twenty CRM on Psykos WSL2 (:3020)
> Last updated: 2026-08-27 (V5.5)

## Current State
- Twenty CRM: Live on Psykos WSL2 `http://100.64.0.4:3020` (HTTP 200, SPA)
- OSINT recon script: `makima-hermes/scripts/osint_recon.py`
- Domain: Telarus enterprise prospecting pipeline (bryce@iop.llc)

## Integration Points

### Phase A: OSINT Results → Twenty CRM Contacts
The osint_recon.py performs DNS/MX/SPF/DMARC/ASN/WHOIS recon on target domains.
Results can be pushed to Twenty CRM as contacts/companies via the CRM API.

**Twenty CRM API:**
- Base URL: `http://100.64.0.4:3020/api/v1/`
- Auth: Bearer token (configure in Twenty CRM settings)
- Create person: `POST /api/v1/people`
- Create company: `POST /api/v1/companies`

**Proposed pipeline:**
1. `python3 osint_recon.py --json <domain>` → JSON output with DNS/MX/WHOIS data
2. Parse extracted company info (org name, emails, addresses from WHOIS)
3. `POST /api/v1/companies` to create company record
4. `POST /api/v1/people` to add associated contacts
5. Link recon data as notes or custom fields

### Phase B: Activepieces Webhook Bridge
Activepieces on Saitama (:8181) can serve as the automation layer:
1. OSINT recon completes → webhook fires
2. Activepieces workflow transforms data → pushes to Twenty CRM
3. CRM records enriched with domain intel

## Genos Migration Plan

### Why migrate Twenty CRM to Genos?
- Psykos WSL2 is the primary strategic daemon — heavy DB workloads compete with it
- Genos (OCI ARM64, 24GB RAM) has capacity for CRM + pgvector
- Centralizes knowledge graph (Mem0, Neo4j, Qdrant) alongside CRM data

### Migration Steps (Draft)
1. Export Twenty CRM data from current Postgres (pg_dump)
2. Install Twenty CRM on Genos via Docker Compose
3. Import data into Genos Postgres instance
4. Update DNS/routing: `100.64.0.5:3020` replaces `100.64.0.4:3020`
5. Update all client configs (Activepieces, OSINT scripts, dashboard links)
6. Verify data integrity + API functionality
7. Decommission Twenty CRM on Psykos WSL2

### Blockers
- Genos ARM64 compatibility with Twenty CRM (verify Docker image supports ARM)
- Data migration timing (requires downtime window)
- Port allocation: verify :3020 is free on Genos

## Status
- [x] Twenty CRM live on Psykos :3020
- [x] OSINT recon script relocated to makima-hermes
- [ ] OSINT → Twenty CRM API integration (pending operator go)
- [ ] Activepieces webhook bridge (pending)
- [ ] Genos migration (draft plan only — operator approval required)
