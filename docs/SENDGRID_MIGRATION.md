# SendGrid migration safety gate

`src/sendgrid_migration_dry_run.py` is the first production migration gate. It
is deliberately incapable of importing contacts, changing suppressions,
sending mail, or scheduling campaigns.

It reads the complete Mailchimp audience by status, inventories the tags,
segments, merge fields, and welcome journey used by TWY, then reconciles every
source email with SendGrid contacts and suppression/failure state. The only
non-GET provider request it permits is SendGrid's read-only exact-email lookup:
`POST /v3/marketing/contacts/search/emails`.

The mapping fails closed:

- SendGrid spam, bounce, block, or invalid state becomes `cleaned_denylist`.
- SendGrid global/group suppression becomes `marketing_suppressed`.
- Mailchimp `cleaned` becomes `cleaned_denylist`.
- Mailchimp `unsubscribed` becomes `marketing_suppressed`.
- Mailchimp `archived` becomes `archived_excluded` and is neither imported nor
  suppressed.
- Pending, transactional, missing, conflicting, or unknown state becomes
  `quarantine`.
- Only an uncontradicted Mailchimp `subscribed` record is `deliverable`.

Each run writes an immutable, private evidence directory under
`twy_paths.sendgrid_migration_dir(run_id)`. A gate passes only when the welcome
journey matches the accepted backup, all code-referenced targeting dependencies
exist, no source coverage error exists, no contact is quarantined, and the
endpoint audit contains no mutation.

The run also writes four mutually exclusive purpose-specific manifests:

- `deliverable_contacts.json`
- `marketing_suppressions.json`
- `cleaned_denylist.json`
- `archived_exclusions.json`

Inactive manifests contain only the normalized email, effective timestamp,
reason, and source status. Names, merge fields, tags, roles, list membership,
and engagement history are discarded. Archived exclusions are migration
evidence only; future writers must reject `archived_exclusions.json` as a
contact or suppression input.

Example:

```bash
PYTHONPATH=src:/root/twy/paths \
python3 src/sendgrid_migration_dry_run.py \
  --run-id production_YYYYMMDDTHHMMSSZ_pass1 \
  --proof-manifest /root/twy/data/sendgrid_proofs/sg-migration-proof-20260723/teardown_inventory.json \
  --journey-backup-dir /root/twy/data/mailchimp_backups/2026-07-23/api-snapshot-20260724T004100Z.complete/journeys/3209
```

An exit status of `3` is a successfully completed dry run whose migration gate
is blocked. It must never be treated as permission to import or send.
