# twy-announce - Current Tasks

**Last Updated:** 2026-02-08

---

## 🔄 In Progress

None currently.

---

## 📋 Next Up (Priority Order)

### 1. Add Sales Data to Daily Status Report
**Priority:** High  
**Effort:** 2-3 hours  
**Dependencies:** Marvelous API access

**Description:**
- Add Sales section to daily status report
- Fetch sales/revenue data from Marvelous
- Display with historical comparisons (week/month/year over week)

---

## 🔄 Recurring Tasks

### Daily Status Report
- **Marvelous JWT refresh:** Hourly via cron (`src/refresh_jwt.py`)
- **Mailchimp subscriber data:** Daily before report (`src/mailchimp_subscriber_data.py`)
- **Daily report:** 6am MT (`src/daily_status_report.py`)

### Email Reminders
- **Marvelous event sync:** Twice daily at 9am/6pm (`scripts/refresh_marvelous_events.py`)
- **Email reminders:** Every 30 min (`scripts/run_class_email_reminders.sh`)

---

## ✅ Recently Completed

### 2026-02-08: Mailchimp Subscriber Integration
- Added `src/mailchimp_subscriber_data.py` to fetch and cache subscriber counts
- Updated `src/daily_status_report.py` to display Subscribers section
- Historical tracking with week/month/year over week comparisons

### 2026-02-08: Daily Status Report - Historical Tracking
- Added daily snapshots to `data/marvelous/history/`
- Week/month/year over week comparisons for membership

### 2026-02-02: Google Docs Tab Support
- Updated email reminder script to read month-specific tabs
- Added flexible date format parsing

### 2026-01-20: Marvelous API Discovery
- Documented internal Namastream API
- Created `src/marvelous_client.py` library
- Implemented event caching for join links

### 2026-01-20: Email Reminder Pipeline
- Google Doc parsing for class schedules
- Gmail API integration for sending reminders
- State tracking to prevent duplicate sends
- Deployed on Hetzner with cron

---

## ⏸️ Paused

### WhatsApp Automation
**Reason:** Email reminders working well; WhatsApp automation lower priority  
**Unblock:** Resume when WhatsApp posting becomes a priority

### Google Drive Document Parser (for WhatsApp)
**Reason:** Depends on WhatsApp automation  
**Unblock:** Resume after WhatsApp POC

---

## 🚫 Blocked

None.

---

## Notes

**Platform Flexibility:**
- Marvelous integration designed to be modular
- User mentioned platform may change in future

**Current Production Systems:**
- Email reminders: Running on Hetzner via cron
- Daily status report: Running on Hetzner via cron
- Marvelous sync: Running on Hetzner via cron
