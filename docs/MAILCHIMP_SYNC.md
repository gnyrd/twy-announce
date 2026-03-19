# Mailchimp Sync (HeyMarvelous → Mailchimp)

Automated sync of Marvelous membership state into Mailchimp tags.

## What the sync does

`src/sync_mailchimp.py` performs two report-driven actions:

1. **Active membership sync** from Marvelous report `users/15`.
2. **Cancellation processing** from Marvelous report `users/14`.

This follows the rule that Marvelous reports are the source of truth for membership state.

## Current sync behavior

### Active contacts (`users/15`)
- Ensures `Status - Member` is present.
- Ensures the correct membership tag is present:
  - `Membership - Yoga Lifestyle`
  - `Membership - TWY Archive`
- Removes stale state tags when a contact is active:
  - `Status - Lead`
  - `Status - Yoga Lifestyle - Canceled`
  - `Status - TWY Archive - Canceled`
- Removes stale membership tags not present in the active report.

### Canceled / inactive contacts (`users/14`)
- Only contacts present in canceled report rows are canceled automatically.
- Cancellation action:
  - remove `Status - Member`
  - add canceled tag (`Status - Yoga Lifestyle - Canceled` or `Status - TWY Archive - Canceled`)
- Contacts that are inactive but **not** in canceled rows are intentionally left unchanged.

### Manual role-tag overrides
The sync also applies role tags for these known contacts:
- `tiffany@tiffanywoodyoga.com` → `Role - Owner`
- `jp.gan@gmx.com` → `Role - Admin`
- `admin@tiffanywoodyoga.com` → `Role - Admin`
- `vaughn.laurie@gmail.com` → `Role - Admin`

## Authentication and report fetching

Report access uses `src/marvelous_report_jwt.py` (JWT embed token flow), not cookie scraping.

- JWT cache file: `.jwt_cache.json`
- Credentials loaded from `.env`
- If needed, JWT refresh uses Playwright login flow.

### Important: canceled report JWT refresh
Canceled report fetch now forces JWT refresh on every sync run to avoid stale embedded date windows.

## Configuration (`.env`)

Required:

```bash
MAILCHIMP_API_KEY=...
MAILCHIMP_AUDIENCE_ID=...
```

Marvelous auth (required for JWT refresh):

```bash
MARVELOUS_TWY_USERNAME=...
MARVELOUS_TWY_PASSWORD=...
MARVELOUS_SECONDARY_PASSWORD=...
```

Optional:

```bash
# Report IDs/categories
MARVELOUS_ACTIVE_SUBS_REPORT_ID=15
MARVELOUS_ACTIVE_SUBS_REPORT_CATEGORY=users
MARVELOUS_CANCELED_SUBS_REPORT_ID=14
MARVELOUS_CANCELED_SUBS_REPORT_CATEGORY=users

# Force refresh globally (active report path respects this)
MARVELOUS_FORCE_JWT_REFRESH=true

# Local CSV overrides (testing)
MARVELOUS_ACTIVE_SUBSCRIPTIONS_CSV=data/reports/active_subscriptions_....csv
MARVELOUS_CANCELED_SUBSCRIPTIONS_CSV=data/reports/canceled_subscriptions_....csv

# No-write mode
DRY_RUN=1
```

## Usage

From repo root:

```bash
# Dry run (no Mailchimp writes)
DRY_RUN=1 python3 src/sync_mailchimp.py

# Live sync
python3 src/sync_mailchimp.py

# Wrapper with timestamped logs
./scripts/run_mailchimp_sync.sh
```

## Scheduling

Current schedule: **once daily at 1:00 AM**.

Example cron entry:

```bash
0 1 * * * /root/twy/announce/scripts/run_mailchimp_sync.sh
```

## Logs and artifacts

- Wrapper logs: `logs/mailchimp_sync_YYYYMMDD_HHMMSS.log`
- Report snapshots: `data/reports/active_subscriptions_*.csv` and `data/reports/canceled_subscriptions_*.csv`

## Safety checks

- Use `DRY_RUN=1` before live changes.
- Ensure report row counts and summary stats look correct.
- Investigate any `inactive left unchanged` contacts and confirm whether cancellations exist outside the current canceled report window.
