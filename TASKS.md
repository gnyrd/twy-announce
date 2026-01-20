# twy-whatsapp-poster - Current Tasks

**Last Updated:** 2025-11-20

---

## 🔄 In Progress

### WhatsApp Automation Proof of Concept
**Status:** ⏳ Active  
**Priority:** Critical  
**Estimated Effort:** 2-4 hours  
**Deadline:** ASAP (blocks all other work)

**Goal:** Validate that WhatsApp posting works reliably before building rest of system.

**Steps:**
- 🔄 Set up Node.js project with whatsapp-web.js
- 🔄 Create authentication script
- 🔄 Test connecting to WhatsApp Web
- 🔄 Test posting to target group
- 🔄 Document any rate limits or restrictions
- 🔄 Assess reliability and risk of account restrictions

**Acceptance Criteria:**
- Can authenticate to WhatsApp Web
- Can send test message to target group
- No immediate account warnings/restrictions
- Documented any limitations or risks

---

## 📋 Next Up (Priority Order)

### 1. Google Drive Document Analysis
**Priority:** High  
**Effort:** 1-2 hours  
**Dependencies:** Need access to the Google Drive document

**Description:**
- Get link to or copy of the scheduling document
- Analyze format and structure
- Design parser strategy
- Identify edge cases (formatting inconsistencies)

**Blockers:** Waiting for user to provide document access

---

### 2. Google Drive Parser Implementation
**Priority:** High  
**Effort:** 3-5 hours  
**Dependencies:** Task #1 complete, WhatsApp POC successful

**Description:**
- Set up Google Drive API credentials
- Implement document fetcher
- Parse event data (dates, times, class names)
- Extract relevant content for posts
- Handle formatting inconsistencies
- Unit tests for parser

---

### 3. Marvelous Platform Investigation
**Priority:** Medium  
**Effort:** 2-3 hours  
**Status:** ✅ Completed (2026-01-20)  
**Dependencies:** None (can be done in parallel)

**Description:**
- Confirmed use of internal Namastream API for events.
- Implemented `scripts/refresh_marvelous_events.py` to cache a trimmed 60-day window under `data/marvelous_events.json`.
- Wired reminder pipeline to match classes to Marvelous events by start time/title and inject `event/details/<id>` join links, falling back to the calendar URL when unmatched.

**Notes:**
- Studio slug and URLs are hard-coded; if Tiffany Wood Yoga changes platforms or domains, update the Marvelous constants.
- API behavior is unofficial and may change; logs and sync failures should be monitored.

---

### 4. Message Template System
**Priority:** Medium  
**Effort:** 2-3 hours  
**Dependencies:** Google Drive parser design (Task #1)

**Description:**
- Design message template structure
- Support variable substitution (class name, date, time, link)
- Make templates configurable
- Handle missing data gracefully (e.g., no Marvelous link)

---

### 5. Scheduler Implementation
**Priority:** Medium  
**Effort:** 3-4 hours  
**Dependencies:** WhatsApp POC, Google Drive parser complete

**Description:**
- Set up APScheduler
- Configure daily check time (configurable, default mid-afternoon MT)
- Check Google Drive for next day's events
- Generate and post messages
- Logging and error handling
- Dry-run mode for testing

---

### 6. Local Deployment Setup
**Priority:** Medium  
**Effort:** 2-3 hours  
**Dependencies:** All core components working

**Description:**
- Create launch script
- Set up launchd plist for auto-start
- Environment variable management
- Log file rotation
- Graceful shutdown handling

---

### 7. Testing & Reliability
**Priority:** Medium  
**Effort:** 2-3 hours  
**Dependencies:** Core components complete

**Description:**
- End-to-end integration tests
- Error recovery testing
- Rate limit handling
- Network failure handling
- Dry-run mode validation

---

### 8. Email reminder pipeline (Google Doc → Gmail)
**Priority:** High  
**Effort:** 4-6 hours  
**Status:** ✅ Completed (2026-01-20)  
**Dependencies:** Access to class schedule Google Doc; Google API credentials ready

**Description:**
- Fetch class schedule from Google Doc (Salt Lake City timezone / America-Denver).
- Parse classes and compute reminder times at 26/25/24 hours before class.
- Send reminder emails via Gmail API (initially to `jpgan6@gmail.com`) with a copy-pastable WhatsApp block.
- Track reminder send-state in `data/reminder_state.json` so each reminder is sent exactly once.

---

### 9. Hetzner deployment for reminders
**Priority:** High  
**Effort:** 3-4 hours  
**Status:** ✅ Completed (2026-01-20)  
**Dependencies:** Email reminder pipeline implemented (Task 8)

**Description:**
- Run `scripts/send_class_email_reminders.py` on `twy-hetzner` using `.env` + Gmail token files.
- Configure cron on Hetzner to:
  - Sync Marvelous events twice daily via `scripts/refresh_marvelous_events.py` (09:00 and 18:00 local time).
  - Run `scripts/run_class_email_reminders.sh` every 30 minutes with `REMINDER_OFFSETS=26`.
- Log output to `logs/marvelous_sync.log` and `logs/reminders.log` for debugging.

---

## 🔄 Recurring Tasks

None yet - will add monitoring tasks once deployed.

---

## ⏸️ Paused

None.

---

## ✅ Recently Completed

None yet.

---

## 🚫 Blocked

**Blocked tasks will be listed here with:**
- ❌ Task name
- Blocking reason
- What's needed to unblock

Currently none.

---

## Notes

**WhatsApp Automation Risk Assessment Needed:**
- Using unofficial WhatsApp Web API (whatsapp-web.js)
- Could result in account restrictions/bans
- Need to understand risks before proceeding with full build
- Consider fallback options if WhatsApp automation proves unreliable

**Platform Flexibility:**
- Marvelous integration should be modular
- User mentioned platform may change in future
- Design with easy swapping in mind

**Configuration Priority:**
- Posting time must be configurable (default mid-afternoon Mountain Time)
- Support for timezone changes
- Dry-run mode for safe testing
