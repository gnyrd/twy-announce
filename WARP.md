# twy-announce-poster

**WhatsApp Group Admin Automation System**

Last Updated: 2026-02-09

---

## Overview

Automated system for posting class announcements to a WhatsApp group based on events scheduled in a Google Drive document. Posts are made the day before each event, mid-afternoon Mountain Time.

**Key Components:**
- Google Drive document parser (extracts scheduled events)
- Daily scheduler (configurable posting time)
- Marvelous platform integration (fetches class links)
- WhatsApp automation (posts to group)

---

## Documentation Structure

**Start Here:**
- [System Status](STATUS.md) - Current state and health
- [Current Tasks](TASKS.md) - Work in progress (1-4 weeks)
- [Roadmap](FEATURES.md) - Future plans and ideas
- [Change History](HISTORY.md) - Completed milestones

**Setup Guides:**
- [Getting Started](GETTING_STARTED.md) - Local development setup

---

## Quick Start

**Prerequisites:**
- Python 3.9+
- Node.js 18+ (for WhatsApp Web automation)
- Google Drive API credentials
- WhatsApp account for automation

**Setup:**
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Node.js dependencies
npm install

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Run WhatsApp authentication
npm run whatsapp-auth

# Test posting
python -m twy_whatsapp_poster.test
```

---

## Project Structure

At the moment this project is primarily a Node.js WhatsApp tool, with Python-based scheduler/parser components planned but not yet implemented.

Key pieces that exist today:
- `list_groups.js` – helper to list group IDs.
- `src/send_to_whatsapp.js` – small one-shot CLI (from the former twy-announce-announcer repo) that sends a message to a group by exact name.
- `docs/references/` – sample class source and WhatsApp post examples used for designing templates.

Planned (but not yet present) Python modules mentioned elsewhere in this file (scheduler, Drive parser, Marvelous integration) should be treated as future work.

---

## Key Principles

1. **Reliability First** - Automation must be dependable for daily admin tasks
2. **Configurable** - Posting times, message templates, platforms can change
3. **Maintainable** - Platform integrations (Marvelous) may need replacement
4. **Safe** - Never spam, handle WhatsApp rate limits, graceful failures

---

## AI Agent Rules

### Terminology Guide
When user asks about:
- "current tasks" / "what should I work on" → Read TASKS.md
- "roadmap" / "future plans" → Read FEATURES.md
- "system status" / "what's working" → Read STATUS.md
- "history" / "what's been done" → Read HISTORY.md

### Critical Rules

1. **ALWAYS read WARP.md first** when working in this directory
2. **Check STATUS.md** to understand current system state before any work
3. **Read TASKS.md** before starting new work to avoid duplication
4. **When completing work:**
   - Mark ✅ in TASKS.md
   - Add one-liner to STATUS.md "Recent Changes"
   - After 30 days, migrate to HISTORY.md
5. **Update WARP.md before any git commit** (per user rule)
6. **NEVER update docs** for minor refactoring or internal changes
7. **WhatsApp automation is sensitive** - always test changes carefully
8. **Secrets management** - never expose credentials, use environment variables

### Documentation Update Rules

**Major Changes → Update STATUS.md + TASKS.md:**
- WhatsApp automation changes
- Google Drive parser changes
- Marvelous integration changes
- Scheduler configuration changes
- Breaking changes to message format

**Feature Work → Update TASKS.md:**
- Starting work: Move to "In Progress" with ⏳
- Pausing work: Add ⏸️ with reason
- Completing work: Mark ✅, add to STATUS.md "Recent Changes"
- After 30 days: Migrate ✅ items to HISTORY.md

**Minor Changes → Update specific docs only:**
- Message template tweaks → Update config/templates documentation
- Bug fixes → No doc update unless behavior changes

**No Documentation Required:**
- Code refactoring (no behavior change)
- Internal renaming
- Comment updates
- Log message changes

---


---

## Runtime Environments

### Local Development
- Run WhatsApp tests and one-off posts using the Node CLI toolbox (see next section).
- Safe for manual experiments; does not affect the scheduled reminder pipeline.

### Hetzner Production (Cron)
- Intended deployment is a Hetzner host running the reminder pipeline under cron.
- Cron calls the thin wrapper script `scripts/run_class_email_reminders.sh` approximately every 30 minutes.
- That script:
  - Loads environment from `.env` (if present).
  - Invokes `scripts/send_class_email_reminders.py` with the current time and arguments.
  - Uses `data/reminder_state.json` to track which reminders have already been sent.

**Current cron setup on Hetzner (reference):**
- Marvelous sync (twice daily) –
  - `0 9,18 * * * cd /root/twy-announce && /usr/bin/python3 scripts/refresh_marvelous_events.py >> logs/marvelous_sync.log 2>&1`
- Email reminders (every 30 min) –
  - `*/30 * * * * cd /root/twy-announce && REMINDER_OFFSETS=26 ./scripts/run_class_email_reminders.sh >> logs/reminders.log 2>&1`

- Intended deployment is a Hetzner host running the reminder pipeline under cron.
- Cron calls the thin wrapper script `scripts/run_class_email_reminders.sh` approximately every 30 minutes.
- That script:
  - Loads environment from `.env` (if present).
  - Invokes `scripts/send_class_email_reminders.py` with the current time and arguments.
  - Uses `data/reminder_state.json` to track which reminders have already been sent.

**Example test command (used during development):**
```bash
cd /root/twy-announce
mv data/reminder_state.json data/reminder_state_backup_test.json 2>/dev/null || true
REMINDER_OFFSETS=26 ./scripts/run_class_email_reminders.sh --now 2026-01-14T06:05
```

## WhatsApp Node CLI Toolbox

These tools are optional helpers for talking directly to WhatsApp. They are useful for manual testing and ad-hoc posts, but are **not** required for the Python reminder pipeline.

- `node whatsapp_bot.js auth` – Authenticate the WhatsApp session by scanning a QR code.
- `node whatsapp_bot.js test` – Send a simple test message to the configured group.
- `node whatsapp_bot.js post "Message text"` – Post a custom message to the configured group.
- `node list_groups.js` – List available WhatsApp groups and their IDs.
- `node src/send_to_whatsapp.js "Group Name" "Message text"` – Send a one-off message to a specific group by name.

Use these from your local machine or the Hetzner host when you want to verify WhatsApp connectivity or send a manual message.

## Configuration

**Environment Variables (.env):**
```bash
# Google Drive
GOOGLE_DRIVE_CREDENTIALS_PATH=./credentials/google-drive.json
GOOGLE_DRIVE_DOCUMENT_ID=<document-id>

# Marvelous Platform
MARVELOUS_API_URL=https://heymarvelous.com
MARVELOUS_AUTH_TOKEN=<token-if-needed>

# WhatsApp
WHATSAPP_SESSION_PATH=./sessions/whatsapp
WHATSAPP_GROUP_ID=<group-id>

# Scheduler
POSTING_TIME=14:00
TIMEZONE=America/Denver
DRY_RUN=true
```

---

## Dependencies

### Python
- `google-api-python-client` - Google Drive API
- `APScheduler` - Task scheduling
- `python-dotenv` - Environment configuration
- `requests` - HTTP client for Marvelous API
- `beautifulsoup4` - HTML parsing (if Marvelous scraping needed)
- `selenium` - Browser automation (fallback)

### Node.js
- `whatsapp-web.js` - WhatsApp Web automation
- `qrcode-terminal` - QR code display for authentication

---

## Testing

```bash
# Test Google Drive parser
python -m twy_whatsapp_poster.drive_parser --test

# Test Marvelous integration
python -m twy_whatsapp_poster.marvelous --test

# Test WhatsApp connection (dry run)
npm run whatsapp-test

# Full end-to-end test (dry run)
python -m twy_whatsapp_poster.test --dry-run
```

---

## Deployment

**Local (Mac):**
```bash
# Run as background service
./scripts/start.sh

# Or use launchd for automatic startup
# (launchd plist configuration TBD)
```

**Cloud (Future):**
- Google Cloud Functions or AWS Lambda
- Scheduled with Cloud Scheduler or EventBridge

---

## Known Limitations

1. **WhatsApp automation risk** - Unofficial API may result in account restrictions
2. **Marvelous platform has no official API** - Web scraping may break
3. **Local deployment requires Mac to be on** - Cloud deployment planned for future
4. **Google Drive parsing assumes consistent format** - Manual format changes require parser updates

---

## Contributing

This is a personal automation project. Changes should:
1. Follow the documentation system (STATUS/TASKS/FEATURES/HISTORY)
2. Update WARP.md before committing
3. Include tests for critical paths
4. Handle failures gracefully (this is for admin tasks!)

---

## License

Private project for personal use.

---

## Contact

Project maintained by ganyard for TWEEE WhatsApp group administration.


## 2026-02-08 - Daily Status Report Automation

Implemented automated daily subscription status reports from Marvelous to Slack.

**Features:**
- JWT token refresh automation using Playwright browser automation
- Daily subscription data fetch from Marvelous Metabase reports
- Formatted Slack reports with subscription counts and revenue
- Decoupled JWT refresh (hourly) from report generation (daily at 6am MT)

**Architecture:**
1. **JWT Refresh (`src/refresh_jwt.py`)**
   - Logs into Marvelous via magic code URL
   - Extracts JWT from report iframe using headless Chromium
   - Caches token to `.jwt_cache.json`
   - Runs hourly via cron (tokens expire in ~2 hours)

2. **Daily Report (`src/daily_status_report.py`)**
   - Loads cached JWT token
   - Fetches Report 56 (Active Subscriptions by Product) from Metabase
   - Formats data with proper sorting (Monthly before Other)
   - Posts to Slack via webhook
   - Runs daily at 6am Mountain Time

**Configuration (in .env):**
- `MARVELOUS_MAGIC_URL` - Dashboard access URL with magic code
- `MARVELOUS_SECONDARY_PASSWORD` - Authentication password
- `SLACK_WEBHOOK_URL` - Slack incoming webhook for posting

**Cron Jobs:**
```bash
# JWT refresh every hour
0 * * * * cd /root/twy-announce && python3 src/refresh_jwt.py

# Daily report at 6am MT (1pm UTC)
0 13 * * * cd /root/twy-announce && python3 src/daily_status_report.py
```

**Dependencies Added:**
- `playwright` - Browser automation for JWT extraction
- Chromium browser installed via `playwright install chromium`

**Security:**
- JWT cache file (`.jwt_cache.json`) excluded from git
- Credentials stored in `.env` (gitignored)
- Tokens refreshed automatically, never hardcoded


**Historical Data Tracking (Added 2026-02-08):**
- Daily snapshots saved to `data/marvelous/history/{date}.json`
- Conditional comparisons shown when data exists:
  - Week-over-week (7 days ago)
  - Month-over-month (30 days ago)
  - Year-over-year (365 days ago)
- Comparisons display subscription and revenue changes with directional arrows
- Gracefully handles missing historical data (comparisons appear as data accumulates)

**Future Enhancements:**
- Historical data tracking for week-over-week and month-over-month comparisons
- Additional Marvelous reports integration
- Alert notifications for subscription changes

## 2026-01-20 - HeyMarvelous API Discovery & Client Library

### Summary
Discovered and documented the undocumented HeyMarvelous (Namastream) API through systematic HAR file analysis. Created a comprehensive Python client library with full CRUD support for events, products, coupons, customers, and partial support for media library items.

### What Was Added

**New Files:**
- `src/marvelous_client.py` - Complete Python client library (30+ methods)
- `docs/MARVELOUS_API.md` - Complete API documentation
- `docs/MARVELOUS_CLIENT_README.md` - Library usage guide
- `examples/marvelous_example.py` - Working code examples

**Data Files (for investigation):**
- `data/app.heymarvelous.com.*.har` - HAR files used for API discovery (8 files)

### API Coverage

**Fully Implemented:**
- Events - Full CRUD + public listing
- Products - Full CRUD + tags listing
- Coupons - Full CRUD + pagination + stats
- Customers - Full CRUD + pagination

**Partially Implemented:**
- Media Library Items - GET, UPDATE, LIST (creation requires file upload workflow)

### Key Features

1. **Authentication Support** - Two-step flow (email/password → magic code → token)
2. **Automatic Object Conversion** - Handles nested object to ID conversion for updates
3. **EditorJS Support** - Helper methods for rich text descriptions
4. **Pagination** - Support for paginated list endpoints
5. **Type Hints** - Full type annotations throughout
6. **Error Handling** - Custom exceptions (MarvelousAPIError, MarvelousAuthError)

### Technical Details

- **Base URL:** https://api.namastream.com
- **Auth Method:** Token-based (Authorization: Token {key})
- **Common Pattern:** GET returns full objects, PUT expects IDs for nested references
- **Pagination:** 10-12 items per page depending on resource
- **Media Storage:** Videos hosted on Vimeo (not directly downloadable)

### Known Limitations

1. Media library file uploads not yet implemented (requires upload workflow investigation)
2. Token expiration behavior unknown
3. Rate limiting not documented
4. Some instructor/product IDs must be obtained from existing data

### High Priority TODO

- Complete media library items implementation (file upload workflow)
- Test DELETE endpoint for media items
- Investigate upload endpoints thoroughly

### Testing

All CRUD operations tested via API:
- Created, read, updated, and deleted test items for all resources
- Verified pagination works correctly
- Confirmed nested object conversion logic
- Tested with 1285+ media items, 566+ customers, 71+ coupons

### Usage Example

```python
from marvelous_client import MarvelousClient

client = MarvelousClient(auth_token="your-token")

# Events
event_id = client.create_event(...)
client.update_event(event_id, event_name="Updated")
client.delete_event(event_id)

# Products
product_id = client.create_product(...)
tags = client.list_product_tags()

# Coupons
coupon_id = client.create_coupon(...)
stats = client.get_coupon_stats()

# Customers
customer_id = client.create_customer(...)
customers = client.list_customers(page=1)

# Media
media_items = client.list_media(page=1)
client.update_media(media_id, title="New Title")
```

### References

- API Documentation: `docs/MARVELOUS_API.md`
- Library Guide: `docs/MARVELOUS_CLIENT_README.md`
- Examples: `examples/marvelous_example.py`
- HAR Files: `data/app.heymarvelous.com.*.har`


---

## 2026-02-07: Repository Renamed from twy-whatsapp to twy-announce

### Changes Made
Renamed repository to better reflect its broader purpose:

**Updated References:**
- Changed package name from `twy-whatsapp-poster` to `twy-announce` in package.json and package-lock.json
- Updated all documentation files (README.md, WARP.md, STATUS.md, FEATURES.md, HISTORY.md, TASKS.md, QUICKREF.md, GETTING_STARTED.md)
- Updated configuration files (.env.example, whatsapp_bot.js)
- Updated Marvelous client documentation (docs/MARVELOUS_CLIENT_README.md)
- Changed git remote URL from git@github.com:gnyrd/twy-whatsapp.git to git@github.com:gnyrd/twy-announce.git
- Renamed directory from /root/twy-whatsapp to /root/twy-announce

**Rationale:**
The new name "twy-announce" better reflects that this system handles various announcement channels (email, WhatsApp, etc.) rather than being WhatsApp-specific.


## 2026-02-02: Enhanced Email Reminder Script

### Changes Made
Updated `scripts/send_class_email_reminders.py` to support Google Docs tabs and flexible date formats:

**Document Tab Support:**
- Script now uses Google Docs API with `includeTabsContent=True` parameter
- Automatically detects and reads the tab matching current month/year (e.g., "February 2026")
- Falls back to plain text export if tabs are not available
- Adds helpful logging to show which tab is being read

**Flexible Date Format Support:**
- Added `is_class_heading()` helper function to detect class entry headers
- Now supports both date formats:
  - Original: "Monday, Jan 5 — Stretch & Strength"
  - New: "FEB 2 – Stretch & Strength"
- Uses regex pattern matching for month abbreviations and full names
- Added `import re` for pattern matching

**Impact:**
- Fixed issue where February 2026 classes weren't being parsed
- System can now handle different formatting styles across months
- More robust for future document structure changes

**Testing:**
- Verified script can read "February 2026" tab
- Successfully parsed all February classes
- Force-sent missed reminder for Feb 2 class to all recipients

### Files Modified
- `scripts/send_class_email_reminders.py` (+95, -8 lines)

### Commit
- Hash: 4150264
- Message: "Support Google Docs tabs and flexible date formats"

---

## 2026-02-02: Simplified Email Reminder Formatting

### Problem
Email reminders were not displaying class details correctly for February classes. The parser was trying to extract individual fields (description, affirmation, etc.) but the February format uses different field names and the extraction logic was failing.

### Solution
Simplified the email format to include all raw content from the class entry between the heading and "Required Items" line. This makes the system more robust to format changes.

### Changes Made

**ClassEntry dataclass:**
- Added `raw_content: str | None` field to store the full content block

**parse_block function:**
- Extract all content lines between heading and "Required Items"
- Join with double newlines to create `raw_content` field
- Still extract title from "Title/Theme:" or heading

**build_email function:**
- Check for `raw_content` field first
- If present, use raw content as-is in the email
- Otherwise, fall back to legacy format with individual parsed fields

**Email format now:**
```
✨ Join Tiff for class on February 02, 2026 at 5:30 PM MST

Title/Theme: Grounding Boundaries

Energetic Pulse: Establish containment; root to stabilize...

Apex Pose: Parsvottanasana

UPAs: Muscular Energy...

Physical Arc: Standing postures...

Affirmation: Containment is courage.

*Link to Join:* https://studio.tiffanywoodyoga.com/...
```

### Testing
- Verified Feb 2 and Feb 3 reminder emails show correct format
- Re-sent corrected reminders to all recipients
- Format works for both January (parsed fields) and February (raw content) entries

### Files Modified
- `scripts/send_class_email_reminders.py`
  - Updated ClassEntry dataclass
  - Updated parse_block to extract raw_content
  - Updated build_email to use raw_content when available
  - Fixed norm_label to properly extract label portion before colon
  - Updated title extraction to handle "Title/Theme:" field

## 2026-02-08: Mailchimp Subscriber Data Integration

### Summary
Added Mailchimp subscriber count to the daily status report, displayed above the Membership section.

### Changes Made

**New Files:**
- `src/mailchimp_subscriber_data.py` - Fetches subscriber count from Mailchimp and saves daily snapshots

**Modified Files:**
- `src/daily_status_report.py` - Loads cached Mailchimp data and displays Subscribers section
- `requirements.txt` - Added `mailchimp3` dependency

**Data Storage:**
- Snapshots saved to `data/mailchimp/history/{date}.json`
- Format: `{"date": "YYYY-MM-DD", "timestamp": "ISO", "subscriber_count": N}`

**Report Output:**
```
*TWY Daily Status Report*
Sunday, Feb 08, 2026

*Subscribers:*
 Total: 924

  Week over week: +12
  Month over month: +45
  Year over year: +200

*Membership:*
 Active Students: 150
 ...
```

**Configuration (.env):**
- `MAILCHIMP_API_KEY` - API key for Mailchimp
- `MAILCHIMP_AUDIENCE_ID` - Audience/list ID to track

**Usage:**
```bash
# Run mailchimp data fetch first
python3 src/mailchimp_subscriber_data.py

# Then run daily report
python3 src/daily_status_report.py
```

**Pattern:**
Follows same pattern as Marvelous data - separate script fetches and caches data, daily report reads from cache.

## 2026-02-09: Path Portability Fix for Instagram Script

### Summary
Fixed hardcoded `/root` paths in `instagram_follower_data.py` to make the script portable across different environments (macOS, Linux, Docker).

### Changes Made

**Modified Files:**
- `src/instagram_follower_data.py` - Updated path configuration

**Path Changes:**
- `SESSION_FILE`: Changed from `/root/.config/instaloader/session-tiffanywoodyoga` to `Path.home() / ".config/instaloader/session-tiffanywoodyoga"` (dynamically resolves to user's home directory)
- `INSTAGRAM_HISTORY_DIR`: Changed from `/root/twy-announce/data/instagram/history` to `Path(__file__).parent.parent / "data/instagram/history"` (relative to script location)

**Benefits:**
- Script now works on macOS development environment (`/Users/admin/...`)
- Still works in production Docker/Linux environment (`/root/...`)
- Data directory is relative to project root, making the script relocatable

### Configuration Update

**~/.zshrc:**
- Added `/Users/admin/Library/Python/3.9/bin` to PATH for Python package scripts (normalizer, pyrsa-*, instaloader, google-oauthlib-tool)


## 2026-02-09: Product Breakdown Format Improvement

### Summary
Reformatted the product breakdown section in the daily status report to consolidate Monthly/Annual billing cycles on a single line with per-product historical comparisons.

### Changes Made

**Modified Files:**
- `src/daily_status_report.py` - Updated `format_report()` function and added `get_product_counts()` helper

**New Format:**
```
 TWY Archive (Monthly/Annual): 1 / 6 students
   𝚫 week:  +1 / +2
   𝚫 month: +1 / +2
   𝚫 year:  +1 / +2

 The Yoga Lifestyle Membership (Monthly/Annual): 29 / 2 students
   𝚫 week:   +3 / +1
   𝚫 month: +10 / +0
   𝚫 year:  +15 / +2
```

**Features:**
- Monthly and Annual counts displayed on same line with ` / ` separator
- Per-product week/month/year deltas shown below each product
- Column alignment within each product block (slashes line up vertically)
- Blank line between product blocks for readability
- "Other" billing cycle displayed as "Annual" in the label

## 2026-02-09: Instagram Follower Data Integration

### Summary
Added Instagram follower count to the daily status report Subscribers section.

### Changes Made

**New Files:**
- `src/instagram_follower_data.py` - Fetches follower count from Instagram using instaloader and saves daily snapshots

**Modified Files:**
- `src/daily_status_report.py` - Loads cached Instagram data and displays in Subscribers section with per-metric deltas
- `requirements.txt` - Added `instaloader` dependency

**Data Storage:**
- Snapshots saved to `data/instagram/history/{date}.json`
- Format: `{"date": "YYYY-MM-DD", "timestamp": "ISO", "follower_count": N}`

**Report Output:**
```
*Subscribers:*
 Email: 924
   Δ week:  +10
   Δ month: +50
 Instagram: 2,298
   Δ week:  +25
   Δ month: +100
```

**Session Authentication:**
- Uses instaloader session file at `/root/.config/instaloader/session-tiffanywoodyoga`
- Session must be created on the server due to Instagram IP restrictions
- Run `instaloader --login tiffanywoodyoga` on server to create/refresh session

**Important: Instagram blocks datacenter IPs**
- The `instagram_follower_data.py` script must be run from a non-datacenter IP (e.g., local machine)
- Copy the resulting JSON file to the server before running daily status report
- Local path: `~/Repos/twy-announce/data/instagram/history/{date}.json`
- Server path: `/root/twy-announce/data/instagram/history/{date}.json`

**Usage:**
```bash
# On local machine (Instagram fetch)
python3 src/instagram_follower_data.py
scp data/instagram/history/YYYY-MM-DD.json root@SERVER:/root/twy-announce/data/instagram/history/

# On server (daily report)
python3 src/daily_status_report.py
```
