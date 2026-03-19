# twy-announce-poster - Current Status

**Last Verified:** 2026-03-19
**Version:** v0.1.1 (WhatsApp tools consolidated)

---

## System Health

🟡 Development Phase - Email reminders operational; WhatsApp auto-posting still in development

### Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| Google Drive Parser | ⏳ In Development | Testing approach for WhatsApp automation |
| Scheduler | 📋 Planned | Pending WhatsApp proof of concept |
| Marvelous Integration | ⏳ In Development | Events cached locally; join links fetched from Marvelous |
| WhatsApp Automation | ⏳ In Development | **Current focus** |

---

## Active Components

- Email reminder pipeline (Google Doc → Gmail) – ✅ Running on Hetzner via cron every 30 minutes.
- Marvelous event cache – ✅ `scripts/refresh_marvelous_events.py` runs twice daily to update `data/marvelous_events.json`.

---

## Recent Changes

**2026-03-19:**
- Added Marvelous report-driven Mailchimp membership sync (`src/sync_mailchimp.py`) using active report `users/15` and canceled report `users/14`.
- Added JWT-based report fetch helper (`src/marvelous_report_jwt.py`) and daily Mailchimp sync wrapper script (`scripts/run_mailchimp_sync.sh`).
- Updated cancellation flow to refresh canceled-report JWT on every sync run so embedded report date windows stay current.
- Added explicit role tagging for key accounts (`Role - Owner` / `Role - Admin`) in sync processing.

**2026-01-20:**
- Consolidated older `twy-announce-announcer` CLI into this repo (`src/send_to_whatsapp.js`).
- Added class source and WhatsApp post examples under `docs/references/` for template design.
- Confirmed direction to run scheduling/reminder logic on Hetzner instead of an always-on Mac.

**2025-11-20:**
- Project initialized with STATUS/FEATURES/HISTORY documentation system
- Repository created: `twy-announce-poster`
- Documentation structure established
- **Started:** WhatsApp automation proof of concept

---

## Current Focus

**Phase 1: WhatsApp Automation Proof of Concept**

Testing WhatsApp posting capabilities before building full system:
1. Set up whatsapp-web.js with Node.js
2. Authenticate WhatsApp session
3. Test posting to target group
4. Verify reliability and any rate limits
5. Document any account restrictions or risks

**Why this order:** WhatsApp automation is the highest-risk component. If it doesn't work reliably or results in account restrictions, we need to know before building the rest of the system.

---

## Known Issues

None yet - system not operational.

---

## Blockers

1. **WhatsApp automation viability unknown** - Need to validate unofficial API works for our use case
2. **Google Drive document format unknown** - Need example to design parser
3. **Marvelous integration wired for join links** - Events synced via internal API and cached; monitor for API changes

---

## Environment

**Development Machine:**
- MacOS (local development)
- Python 3.9+
- Node.js 18+
- zsh shell

**Deployment Target:**
- Initially: Local Mac (using launchd or cron)
- Future: Cloud functions (Google Cloud or AWS)

---

## Dependencies

### Installed
- Python: google-api-python-client, google-auth, google-auth-oauthlib, python-dateutil (for Google Doc + Gmail reminder pipeline)
- Node.js: whatsapp-web.js, qrcode-terminal (for WhatsApp Web automation experiments)

### Required Soon
- Python: `google-api-python-client`, `APScheduler`, `python-dotenv`, `requests`
- Node.js: `whatsapp-web.js`, `qrcode-terminal`

---

## Configuration

Not yet configured. See WARP.md for configuration structure.

---

## Quick Health Check

System not yet operational - no health checks available.

---

## Next Milestone

**Complete WhatsApp automation proof of concept** - Validate that posting to group works reliably without account restrictions.
