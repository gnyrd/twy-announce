# twy-whatsapp-poster

**WhatsApp Group Admin Automation System**

Last Updated: 2026-01-20

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
- `src/send_to_whatsapp.js` – small one-shot CLI (from the former twy-whatsapp-announcer repo) that sends a message to a group by exact name.
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
  - `0 9,18 * * * cd /root/twy-whatsapp && /usr/bin/python3 scripts/refresh_marvelous_events.py >> logs/marvelous_sync.log 2>&1`
- Email reminders (every 30 min) –
  - `*/30 * * * * cd /root/twy-whatsapp && REMINDER_OFFSETS=26 ./scripts/run_class_email_reminders.sh >> logs/reminders.log 2>&1`

- Intended deployment is a Hetzner host running the reminder pipeline under cron.
- Cron calls the thin wrapper script `scripts/run_class_email_reminders.sh` approximately every 30 minutes.
- That script:
  - Loads environment from `.env` (if present).
  - Invokes `scripts/send_class_email_reminders.py` with the current time and arguments.
  - Uses `data/reminder_state.json` to track which reminders have already been sent.

**Example test command (used during development):**
```bash
cd /root/twy-whatsapp
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

