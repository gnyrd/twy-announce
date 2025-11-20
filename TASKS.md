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
**Dependencies:** None (can be done in parallel)

**Description:**
- Investigate if heymarvelous.com has an API
- If no API: design web scraping approach
- Determine what data we need (class links? schedules?)
- Document authentication requirements
- Create test cases

**Questions to answer:**
- Do they have an official API?
- What authentication is required?
- Can we get class links programmatically?
- How reliable will this be?

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
