#!/usr/bin/env python3
"""
Sync HeyMarvelous Active Subscriptions report to MailChimp tags.

Daily workflow:
1. Fetch Active Subscriptions report from Marvelous (users/<report_id>)
2. Normalize active contacts by email and membership product
3. Sync membership/status tags to MailChimp
4. Mark members as canceled only when present in Canceled Subscriptions report (users/<report_id>)

Environment Variables Required:
- MAILCHIMP_API_KEY
- MAILCHIMP_AUDIENCE_ID

Environment Variables Optional:
- MARVELOUS_ACTIVE_SUBS_REPORT_ID (default: 15)
- MARVELOUS_ACTIVE_SUBS_REPORT_CATEGORY (default: users)
- MARVELOUS_CANCELED_SUBS_REPORT_ID (default: 14)
- MARVELOUS_CANCELED_SUBS_REPORT_CATEGORY (default: users)
- MARVELOUS_FORCE_JWT_REFRESH (1/true/yes to force token refresh)
- MARVELOUS_ACTIVE_SUBSCRIPTIONS_CSV (optional local CSV override for testing)
- MARVELOUS_CANCELED_SUBSCRIPTIONS_CSV (optional local CSV override for testing)
- DRY_RUN (1/true/yes)
"""

import os
import sys
import csv
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Optional, Any

import requests

# Setup paths relative to script location
SCRIPT_DIR = Path(__file__).parent.absolute()
ANNOUNCE_DIR = SCRIPT_DIR.parent
DATA_DIR = ANNOUNCE_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"

try:
    from marvelous_report_jwt import fetch_report_rows, ReportJWTError
except ImportError as e:
    print(f"❌ Failed to import report JWT helper: {e}")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MailChimpSyncer:
    """Sync active Marvelous subscriptions to MailChimp tags."""

    # Tag names (must match MailChimp exactly)
    TAG_STATUS_MEMBER = "Status - Member"
    TAG_STATUS_LEAD = "Status - Lead"
    TAG_STATUS_YL_CANCELED = "Status - Yoga Lifestyle - Canceled"
    TAG_STATUS_ARCHIVE_CANCELED = "Status - TWY Archive - Canceled"
    TAG_MEMBERSHIP_YL = "Membership - Yoga Lifestyle"
    TAG_MEMBERSHIP_ARCHIVE = "Membership - TWY Archive"
    TAG_ROLE_OWNER = "Role - Owner"
    TAG_ROLE_ADMIN = "Role - Admin"

    # Manual role tagging overrides
    ROLE_TAG_OVERRIDES = {
        "tiffany@tiffanywoodyoga.com": TAG_ROLE_OWNER,
        "jp.gan@gmx.com": TAG_ROLE_ADMIN,
        "admin@tiffanywoodyoga.com": TAG_ROLE_ADMIN,
        "vaughn.laurie@gmail.com": TAG_ROLE_ADMIN,
    }

    # Product mappings
    YOGA_LIFESTYLE_PRODUCTS = ["The Yoga Lifestyle Membership", "Yoga Lifestyle"]
    ARCHIVE_PRODUCTS = ["The Archive", "TWY Archive"]

    def __init__(self, mailchimp_key: str, audience_id: str, dry_run: bool = False):
        self.dry_run = dry_run
        self.mc_key = mailchimp_key
        self.mc_server = mailchimp_key.split('-')[-1]
        self.audience_id = audience_id
        self.base_url = f'https://{self.mc_server}.api.mailchimp.com/3.0'
        self.headers = {
            'Authorization': f'apikey {self.mc_key}',
            'Content-Type': 'application/json'
        }

        self.stats = {
            'active_report_rows_total': 0,
            'canceled_report_rows_total': 0,
            'active_members': 0,
            'members_updated': 0,
            'tags_added': 0,
            'manual_role_tags_added': 0,
            'cancellations': 0,
            'inactive_without_cancel_event': 0,
            'unknown_products': 0,
            'errors': 0,
        }

    def get_member_hash(self, email: str) -> str:
        return hashlib.md5(email.lower().encode()).hexdigest()

    def get_member_tags(self, email: str) -> Optional[Set[str]]:
        email_hash = self.get_member_hash(email)
        url = f'{self.base_url}/lists/{self.audience_id}/members/{email_hash}'

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            member = response.json()
            return {tag['name'] for tag in member.get('tags', [])}
        except requests.RequestException as e:
            logger.error(f"Failed to get tags for {email}: {e}")
            self.stats['errors'] += 1
            return None

    def add_member_if_missing(self, email: str, first_name: str = "", last_name: str = "") -> bool:
        email_hash = self.get_member_hash(email)
        url = f'{self.base_url}/lists/{self.audience_id}/members/{email_hash}'

        # Check if exists
        response = requests.get(url, headers=self.headers, timeout=10)
        if response.status_code == 200:
            return True

        if self.dry_run:
            logger.info(f"[DRY RUN] Would add member: {email}")
            return True

        member_data = {
            'email_address': email,
            'status': 'subscribed',
            'merge_fields': {
                'FNAME': first_name,
                'LNAME': last_name,
            }
        }

        create_url = f'{self.base_url}/lists/{self.audience_id}/members'
        try:
            response = requests.post(create_url, headers=self.headers, json=member_data, timeout=10)
            response.raise_for_status()
            logger.info(f"✓ Added new member: {email}")
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to add member {email}: {e}")
            self.stats['errors'] += 1
            return False

    def update_member_tags(self, email: str, tags_to_add: List[str], tags_to_remove: List[str]) -> bool:
        if self.dry_run:
            if tags_to_add:
                logger.info(f"[DRY RUN] Would add tags to {email}: {tags_to_add}")
            if tags_to_remove:
                logger.info(f"[DRY RUN] Would remove tags from {email}: {tags_to_remove}")
            return True

        email_hash = self.get_member_hash(email)
        url = f'{self.base_url}/lists/{self.audience_id}/members/{email_hash}/tags'

        tags_payload = {
            'tags': [
                *({'name': tag, 'status': 'active'} for tag in tags_to_add),
                *({'name': tag, 'status': 'inactive'} for tag in tags_to_remove),
            ]
        }

        try:
            response = requests.post(url, headers=self.headers, json=tags_payload, timeout=10)
            if response.status_code == 204:
                if tags_to_add or tags_to_remove:
                    logger.info(f"✓ Updated tags for {email}")
                return True
            logger.error(f"Failed to update tags for {email}: HTTP {response.status_code}")
            self.stats['errors'] += 1
            return False
        except requests.RequestException as e:
            logger.error(f"Failed to update tags for {email}: {e}")
            self.stats['errors'] += 1
            return False

    def membership_tag_from_product(self, product_name: str) -> Optional[str]:
        if any(name in product_name for name in self.YOGA_LIFESTYLE_PRODUCTS):
            return self.TAG_MEMBERSHIP_YL
        if any(name in product_name for name in self.ARCHIVE_PRODUCTS):
            return self.TAG_MEMBERSHIP_ARCHIVE
        return None

    def sync_active_contact(self, contact: Dict[str, Any]) -> None:
        email = contact.get('email', '').strip().lower()
        if not email:
            return

        expected_membership_tags: Set[str] = set(contact.get('membership_tags', set()))
        if not expected_membership_tags:
            return

        self.stats['active_members'] += 1

        first_name = contact.get('first_name', '').strip()
        last_name = contact.get('last_name', '').strip()

        if not self.add_member_if_missing(email, first_name, last_name):
            return

        current_tags = self.get_member_tags(email)
        if current_tags is None:
            logger.error(f"Could not get tags for {email}, skipping")
            return

        tags_to_add: List[str] = []
        tags_to_remove: List[str] = []

        # Active subscription => Status - Member
        if self.TAG_STATUS_MEMBER not in current_tags:
            tags_to_add.append(self.TAG_STATUS_MEMBER)

        # Remove lead/canceled states if now active
        if self.TAG_STATUS_LEAD in current_tags:
            tags_to_remove.append(self.TAG_STATUS_LEAD)
        if self.TAG_STATUS_YL_CANCELED in current_tags:
            tags_to_remove.append(self.TAG_STATUS_YL_CANCELED)
        if self.TAG_STATUS_ARCHIVE_CANCELED in current_tags:
            tags_to_remove.append(self.TAG_STATUS_ARCHIVE_CANCELED)

        # Ensure expected membership tags exist
        for membership_tag in sorted(expected_membership_tags):
            if membership_tag not in current_tags:
                tags_to_add.append(membership_tag)

        # Remove stale membership tag(s) not present in active report
        all_membership_tags = {self.TAG_MEMBERSHIP_YL, self.TAG_MEMBERSHIP_ARCHIVE}
        stale_membership_tags = all_membership_tags - expected_membership_tags
        for stale_tag in sorted(stale_membership_tags):
            if stale_tag in current_tags:
                tags_to_remove.append(stale_tag)

        if tags_to_add or tags_to_remove:
            if self.update_member_tags(email, tags_to_add, tags_to_remove):
                self.stats['members_updated'] += 1
                self.stats['tags_added'] += len(tags_to_add)

    def fetch_all_mailchimp_members(self) -> List[Dict[str, Any]]:
        members: List[Dict[str, Any]] = []
        offset = 0
        page_size = 200

        while True:
            url = f'{self.base_url}/lists/{self.audience_id}/members'
            params = {'count': page_size, 'offset': offset}

            payload: Optional[Dict[str, Any]] = None
            last_error: Optional[Exception] = None

            for attempt in range(1, 4):
                try:
                    response = requests.get(url, headers=self.headers, params=params, timeout=45)
                    response.raise_for_status()
                    payload = response.json()
                    break
                except requests.RequestException as e:
                    last_error = e
                    logger.warning(
                        f"Failed to fetch MailChimp members (offset={offset}, attempt={attempt}/3): {e}"
                    )

            if payload is None:
                message = f"Failed to fetch MailChimp members after retries: {last_error}"
                if self.dry_run:
                    logger.warning(message)
                else:
                    logger.error(message)
                    self.stats['errors'] += 1
                return members

            chunk = payload.get('members', [])
            members.extend(chunk)

            total_items = int(payload.get('total_items', len(members)))
            if not chunk or len(members) >= total_items:
                break

            offset += len(chunk)

        return members

    def process_cancellations(
        self,
        active_emails: Set[str],
        canceled_contacts: Dict[str, Dict[str, Any]],
    ) -> None:
        """Apply cancellation tags only for members present in canceled report rows."""
        logger.info("Checking for canceled/lapsed subscriptions...")

        members = self.fetch_all_mailchimp_members()
        canceled_count = 0
        left_unchanged_count = 0

        for member in members:
            email = member.get('email_address', '').lower()
            if not email:
                continue

            tags = {tag['name'] for tag in member.get('tags', [])}

            role_tag = self.ROLE_TAG_OVERRIDES.get(email)
            if role_tag and role_tag not in tags:
                logger.info(f"Applying manual role tag for {email}: {role_tag}")
                if self.update_member_tags(email, [role_tag], []):
                    self.stats['tags_added'] += 1
                    self.stats['manual_role_tags_added'] += 1
                    tags.add(role_tag)

            # Only process contacts currently tagged as members
            if self.TAG_STATUS_MEMBER not in tags:
                continue

            # Skip active contacts
            if email in active_emails:
                continue

            cancel_info = canceled_contacts.get(email)
            if not cancel_info:
                logger.info(f"Inactive member not in canceled report; leaving unchanged: {email}")
                left_unchanged_count += 1
                continue

            canceled_tag = cancel_info.get('canceled_tag')
            if not canceled_tag:
                # Fallback based on last known membership tag
                if self.TAG_MEMBERSHIP_ARCHIVE in tags:
                    canceled_tag = self.TAG_STATUS_ARCHIVE_CANCELED
                else:
                    canceled_tag = self.TAG_STATUS_YL_CANCELED

            tags_to_remove = [self.TAG_STATUS_MEMBER]
            tags_to_add: List[str] = []
            if canceled_tag not in tags:
                tags_to_add.append(canceled_tag)

            if self.update_member_tags(email, tags_to_add, tags_to_remove):
                canceled_count += 1

        logger.info(f"Processed {canceled_count} cancellations from canceled report")
        logger.info(f"Inactive members left unchanged (no canceled report match): {left_unchanged_count}")
        self.stats['cancellations'] = canceled_count
        self.stats['inactive_without_cancel_event'] = left_unchanged_count

    def sync_all(
        self,
        active_contacts: Dict[str, Dict[str, Any]],
        canceled_contacts: Dict[str, Dict[str, Any]],
        active_report_rows_total: int,
        canceled_report_rows_total: int,
    ) -> None:
        self.stats['active_report_rows_total'] = active_report_rows_total
        self.stats['canceled_report_rows_total'] = canceled_report_rows_total
        logger.info(f"Processing {len(active_contacts)} active contacts from report...")

        active_emails = set(active_contacts.keys())

        for email in sorted(active_emails):
            self.sync_active_contact(active_contacts[email])

        self.process_cancellations(active_emails, canceled_contacts)

        logger.info("\n" + "=" * 60)
        logger.info("Sync Complete")
        logger.info("=" * 60)
        logger.info(f"Active report rows: {self.stats['active_report_rows_total']}")
        logger.info(f"Canceled report rows: {self.stats['canceled_report_rows_total']}")
        logger.info(f"Active members found: {self.stats['active_members']}")
        logger.info(f"Members updated: {self.stats['members_updated']}")
        logger.info(f"Tags added: {self.stats['tags_added']}")
        logger.info(f"Manual role tags added: {self.stats['manual_role_tags_added']}")
        logger.info(f"Cancellations processed: {self.stats['cancellations']}")
        logger.info(f"Inactive members left unchanged: {self.stats['inactive_without_cancel_event']}")
        logger.info(f"Unknown products skipped: {self.stats['unknown_products']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info("=" * 60)


def _pick(row: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        if key in row and row[key] is not None:
            value = str(row[key]).strip()
            if value:
                return value
    return ""


def load_active_subscriptions_from_report(
    report_id: int,
    report_category: str,
    force_jwt_refresh: bool = False,
) -> List[Dict[str, Any]]:
    logger.info(f"Loading Active Subscriptions report: /reports/{report_category}/{report_id}")

    try:
        rows = fetch_report_rows(
            report_id=report_id,
            category=report_category,
            # Always refresh JWT for canceled report so its date window stays current.
            force_refresh=True,
        )
    except ReportJWTError as e:
        logger.error(f"Failed loading report via JWT flow: {e}")
        return []
    except requests.RequestException as e:
        logger.error(f"Failed querying report endpoint: {e}")
        return []

    if not rows:
        logger.error("No rows returned from Active Subscriptions report")
        return []

    logger.info(f"✓ Retrieved {len(rows)} report rows")
    return rows


def load_active_subscriptions_from_csv(csv_path: Path) -> List[Dict[str, Any]]:
    logger.info(f"Loading Active Subscriptions rows from CSV: {csv_path}")

    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    logger.info(f"✓ Loaded {len(rows)} rows from CSV")
    return rows


def load_canceled_subscriptions_from_report(
    report_id: int,
    report_category: str,
    force_jwt_refresh: bool = False,
) -> List[Dict[str, Any]]:
    logger.info(f"Loading Canceled Subscriptions report: /reports/{report_category}/{report_id}")

    try:
        rows = fetch_report_rows(
            report_id=report_id,
            category=report_category,
            force_refresh=True,
        )
    except ReportJWTError as e:
        logger.error(f"Failed loading canceled report via JWT flow: {e}")
        return []
    except requests.RequestException as e:
        logger.error(f"Failed querying canceled report endpoint: {e}")
        return []

    if not rows:
        logger.warning("No rows returned from Canceled Subscriptions report")
        return []

    logger.info(f"✓ Retrieved {len(rows)} canceled report rows")
    return rows


def load_canceled_subscriptions_from_csv(csv_path: Path) -> List[Dict[str, Any]]:
    logger.info(f"Loading Canceled Subscriptions rows from CSV: {csv_path}")

    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    logger.info(f"✓ Loaded {len(rows)} canceled rows from CSV")
    return rows


def save_report_rows_csv(rows: List[Dict[str, Any]], output_path: Path) -> None:
    if not rows:
        logger.warning("No report rows to save")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_fields: Set[str] = set()
    for row in rows:
        all_fields.update(row.keys())

    fields = sorted(all_fields)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"✓ Saved {len(rows)} report rows to {output_path}")


def normalize_active_contacts(
    report_rows: List[Dict[str, Any]],
    syncer: MailChimpSyncer,
) -> Dict[str, Dict[str, Any]]:
    contacts: Dict[str, Dict[str, Any]] = {}

    for row in report_rows:
        status = _pick(row, ['Status', 'status']).lower()
        if status and status != 'active':
            continue

        email = _pick(row, ['Email', 'email']).lower()
        if not email:
            continue

        first_name = _pick(row, ['First Name', 'first_name', 'firstName'])
        last_name = _pick(row, ['Last Name', 'last_name', 'lastName'])
        product_name = _pick(row, ['Product Name', 'product_name', 'product'])

        if not product_name:
            logger.warning(f"Missing product name for active row: {email}")
            syncer.stats['unknown_products'] += 1
            continue

        membership_tag = syncer.membership_tag_from_product(product_name)
        if membership_tag is None:
            logger.warning(f"Unknown active subscription product for {email}: {product_name}")
            syncer.stats['unknown_products'] += 1
            continue

        if email not in contacts:
            contacts[email] = {
                'email': email,
                'first_name': first_name,
                'last_name': last_name,
                'membership_tags': set(),
                'products': set(),
            }

        # Keep first non-empty names
        if not contacts[email]['first_name'] and first_name:
            contacts[email]['first_name'] = first_name
        if not contacts[email]['last_name'] and last_name:
            contacts[email]['last_name'] = last_name

        contacts[email]['membership_tags'].add(membership_tag)
        contacts[email]['products'].add(product_name)

    return contacts


def normalize_canceled_contacts(
    canceled_rows: List[Dict[str, Any]],
    syncer: MailChimpSyncer,
) -> Dict[str, Dict[str, Any]]:
    contacts: Dict[str, Dict[str, Any]] = {}

    for row in canceled_rows:
        email = _pick(row, ['Email', 'email']).lower()
        if not email:
            continue

        first_name = _pick(row, ['First Name', 'first_name', 'firstName'])
        last_name = _pick(row, ['Last Name', 'last_name', 'lastName'])
        product_name = _pick(row, ['Product Name', 'product_name', 'product'])

        canceled_tag = syncer.TAG_STATUS_YL_CANCELED
        if product_name:
            membership_tag = syncer.membership_tag_from_product(product_name)
            if membership_tag == syncer.TAG_MEMBERSHIP_ARCHIVE:
                canceled_tag = syncer.TAG_STATUS_ARCHIVE_CANCELED
            elif membership_tag is None:
                logger.warning(f"Unknown canceled subscription product for {email}: {product_name}")
                syncer.stats['unknown_products'] += 1

        contacts[email] = {
            'email': email,
            'first_name': first_name,
            'last_name': last_name,
            'product_name': product_name,
            'canceled_tag': canceled_tag,
        }

    return contacts


def main() -> None:
    env_file = ANNOUNCE_DIR / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
        logger.info(f"Loaded environment from {env_file}")

    mailchimp_key = os.getenv('MAILCHIMP_API_KEY')
    audience_id = os.getenv('MAILCHIMP_AUDIENCE_ID')

    report_id = int(os.getenv('MARVELOUS_ACTIVE_SUBS_REPORT_ID', '15'))
    report_category = os.getenv('MARVELOUS_ACTIVE_SUBS_REPORT_CATEGORY', 'users')
    canceled_report_id = int(os.getenv('MARVELOUS_CANCELED_SUBS_REPORT_ID', '14'))
    canceled_report_category = os.getenv('MARVELOUS_CANCELED_SUBS_REPORT_CATEGORY', 'users')
    force_jwt_refresh = os.getenv('MARVELOUS_FORCE_JWT_REFRESH', '').lower() in ('1', 'true', 'yes')
    report_csv_override = os.getenv('MARVELOUS_ACTIVE_SUBSCRIPTIONS_CSV', '').strip()
    canceled_report_csv_override = os.getenv('MARVELOUS_CANCELED_SUBSCRIPTIONS_CSV', '').strip()

    dry_run = os.getenv('DRY_RUN', '').lower() in ('1', 'true', 'yes')

    if not mailchimp_key or not audience_id:
        logger.error("Missing required environment variables:")
        logger.error("  MAILCHIMP_API_KEY")
        logger.error("  MAILCHIMP_AUDIENCE_ID")
        sys.exit(1)

    if dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN MODE - No MailChimp changes will be made")
        logger.info("=" * 60)

    # Source ACTIVE report rows either from local CSV override or live report fetch
    if report_csv_override:
        report_csv_path = (ANNOUNCE_DIR / report_csv_override).resolve() if not Path(report_csv_override).is_absolute() else Path(report_csv_override)
        if not report_csv_path.exists():
            logger.error(f"Report CSV override not found: {report_csv_path}")
            sys.exit(1)
        report_rows = load_active_subscriptions_from_csv(report_csv_path)
    else:
        report_rows = load_active_subscriptions_from_report(
            report_id=report_id,
            report_category=report_category,
            force_jwt_refresh=force_jwt_refresh,
        )

        # Cache active report snapshot for auditing/reuse
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        report_csv_path = REPORTS_DIR / f"active_subscriptions_{timestamp}.csv"
        save_report_rows_csv(report_rows, report_csv_path)

    if not report_rows:
        logger.error("No active report rows available; aborting")
        sys.exit(1)

    # Source CANCELED report rows either from local CSV override or live report fetch
    if canceled_report_csv_override:
        canceled_csv_path = (ANNOUNCE_DIR / canceled_report_csv_override).resolve() if not Path(canceled_report_csv_override).is_absolute() else Path(canceled_report_csv_override)
        if not canceled_csv_path.exists():
            logger.error(f"Canceled report CSV override not found: {canceled_csv_path}")
            sys.exit(1)
        canceled_report_rows = load_canceled_subscriptions_from_csv(canceled_csv_path)
    else:
        canceled_report_rows = load_canceled_subscriptions_from_report(
            report_id=canceled_report_id,
            report_category=canceled_report_category,
            force_jwt_refresh=force_jwt_refresh,
        )

        # Cache canceled report snapshot for auditing/reuse
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        canceled_csv_path = REPORTS_DIR / f"canceled_subscriptions_{timestamp}.csv"
        save_report_rows_csv(canceled_report_rows, canceled_csv_path)

    syncer = MailChimpSyncer(mailchimp_key, audience_id, dry_run=dry_run)
    active_contacts = normalize_active_contacts(report_rows, syncer)
    canceled_contacts = normalize_canceled_contacts(canceled_report_rows, syncer)

    if not active_contacts:
        logger.error("No active contacts parsed from report; aborting to avoid incorrect cancellations")
        sys.exit(1)

    yl_count = sum(
        1 for c in active_contacts.values() if syncer.TAG_MEMBERSHIP_YL in c['membership_tags']
    )
    archive_count = sum(
        1 for c in active_contacts.values() if syncer.TAG_MEMBERSHIP_ARCHIVE in c['membership_tags']
    )

    logger.info(f"Normalized active contacts: {len(active_contacts)}")
    logger.info(f"  - Yoga Lifestyle members: {yl_count}")
    logger.info(f"  - Archive members: {archive_count}")
    logger.info(f"Normalized canceled contacts: {len(canceled_contacts)}")

    syncer.sync_all(
        active_contacts,
        canceled_contacts,
        active_report_rows_total=len(report_rows),
        canceled_report_rows_total=len(canceled_report_rows),
    )

    sys.exit(1 if syncer.stats['errors'] > 0 else 0)


if __name__ == '__main__':
    main()
