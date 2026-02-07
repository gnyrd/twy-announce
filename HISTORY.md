# twy-announce-poster - Change History

---

## January 2026

### 2026-01-20: Consolidated WhatsApp tools
**Category:** Infrastructure / Documentation  
**Summary:** Merged the older `twy-announce-announcer` CLI and reference docs into this repo.

**Changes:**
- Added `src/send_to_whatsapp.js` one-shot CLI for sending messages by group name.
- Imported class source and WhatsApp post samples into `docs/references/`.
- Updated WARP.md and STATUS.md to reflect consolidated direction and Hetzner-based scheduling.

**Impact:**
- Single canonical repo for WhatsApp automation work.
- Clearer documentation and examples for class-based announcements.

**Rationale:**
- Reduce duplication between small WhatsApp projects.
- Prepare for server-side (Hetzner) scheduling and email reminders.

---
### 2026-01-20: Email reminder pipeline (Google Doc → Gmail)
**Category:** Feature / Infrastructure  
**Summary:** Implemented an email-based reminder system that reads the class plan Google Doc and sends Gmail reminders with copy-pastable WhatsApp blocks.

**Changes:**
- Added `requirements.txt` and `scripts/send_class_email_reminders.py` for the reminder pipeline.
- Added `scripts/run_class_email_reminders.sh` wrapper for cron-friendly execution on Hetzner.
- Updated `.env.example` and docs to document GOOGLE_DOC_ID/TIMEZONE/REMINDER_OFFSETS and Gmail sender/recipient.

**Impact:**
- Enables three reminders (T-26h/T-25h/T-24h) for each class with WhatsApp-ready text.
- Reduces manual rebuild of messages from the Google Doc.

**Rationale:**
- Use Gmail + Google APIs for reliable reminder delivery while keeping WhatsApp automation optional.

---
---
### 2026-01-20: Remove Docker deployment path
**Category:** Chore / Cleanup  
**Summary:** Removed the legacy Docker-based deployment path and its support files; canonical deployment is now the Python reminder pipeline on Hetzner.

**Changes:**
- Deleted Docker-specific files: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `DOCKER.md`, and `Makefile`.
- Cleaned remaining references to Docker from WARP.md, README.md, QUICKREF.md, and STATUS.md.

**Impact:**
- Reduces confusion between historical Docker usage and the current Hetzner + Python/Gmail deployment.
- Simplifies repository by keeping only actively used paths (Python reminders + Node CLI for manual WhatsApp posting).

**Rationale:**
- Docker is no longer used in production; the reminder system runs directly on Hetzner, and WhatsApp messages are posted manually from emailed templates.



## November 2025

### 2025-11-20: Project Initialization
**Category:** Infrastructure  
**Summary:** Created repository and documentation structure

**Changes:**
- Initialized Git repository as `twy-announce-poster`
- Implemented STATUS/FEATURES/HISTORY documentation system
- Created WARP.md with project overview and AI agent rules
- Created STATUS.md to track system health
- Created TASKS.md for tactical work management
- Created FEATURES.md for strategic roadmap
- Created HISTORY.md for completed work archive

**Impact:**
- Clear documentation structure for development
- AI agent can understand project context
- Easy onboarding for future work

**Rationale:**
Applied STATUS-FEATURES-HISTORY-Documentation-System.md framework to maintain clear separation between current state, near-term work, and long-term plans. This reduces documentation maintenance burden while improving clarity.

**Next Steps:**
- Begin WhatsApp automation proof of concept
- Obtain Google Drive document for parser analysis
- Investigate Marvelous platform integration options

---

## Documentation Version History

**v1.0 (2025-11-20):** Initial documentation structure created

---

## Migration Notes

This is a new project, so no migration from existing documentation was needed.
