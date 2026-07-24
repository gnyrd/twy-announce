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

## Production writer

`src/sendgrid_migration_writer.py` is a separate production-capable writer. Its
existence is not permission to run it.

The `plan` command is provider-write-free. It accepts only a completed,
gate-passing evidence directory, validates that all four retention manifests
are exact, complete, minimal, unique, and disjoint, then writes a private
operation plan containing counts and SHA-256 digests rather than copied contact
data.

The `apply` command requires all of:

- the completed evidence directory
- the exact operation plan
- an approval file naming JP
- the exact operation-plan digest on the command line
- a unique apply ID
- an unexpired approval window no longer than 24 hours
- the exact statement
  `APPROVE TWY SENDGRID PRODUCTION CONTACT APPLY`
- target account `admin@tiffanywoodyoga.com`
- exact source, mapping, operation, and count agreement

Before any provider mutation, the writer re-hashes the evidence, rebuilds the
operation plan, checks the approval, verifies the target account through
`GET /v3/user/email`, and rejects any conflicting existing cleaned denylist.

The apply order is suppression-first:

1. Resolve exact production list names and the `twy_status` and `twy_role`
   fields.
2. Resolve or create the recipient-facing `TWY Newsletters` unsubscribe group.
3. Re-read that group by ID and require exact name, description, and default
   state.
4. Add and verify the approved marketing suppressions in that group.
5. Write the local cleaned denylist once, privately and atomically.
6. Upsert only deliverable contacts and wait for every async job.
7. Re-read every deliverable contact and verify its expected list membership.
8. Seal a private apply report.

The writer has no global-suppression, mail-send, deletion, campaign-scheduling,
or Mailchimp endpoint. This matters because SendGrid documents that adding
addresses to a deleted or nonexistent unsubscribe group can place them on the
global suppression list. The writer always verifies the exact group by ID
before adding group-specific suppressions.

`first_name` and `last_name` use SendGrid reserved contact fields.
`twy_status` and `twy_role` resolve to exact custom-field IDs at apply time.
Unsubscribed, cleaned, and archived records never become Marketing Contacts.

Any partial apply remains incomplete. The writer does not delete or roll back
provider objects automatically. A retry requires a fresh read-only
reconciliation and a new approval.

No production apply has been approved or run.

## Suppression enforcement harness

`src/sendgrid_suppression_test.py` is a separately approval-gated test harness.
It has no CLI and has not been run against SendGrid.

It accepts only `admin@tiffanywoodyoga.com` or `jpgan6@gmail.com`, an isolated
list containing exactly that one address, the exact `TWY Newsletters` group,
and the statement
`APPROVE TWY SENDGRID SUPPRESSION ENFORCEMENT TEST`.

The harness adds and verifies the temporary group suppression before creating a
Single Send tagged with the same group. It passes only when SendGrid reports
one request, zero deliveries, zero unique opens, zero unique clicks, and the
suppression still present. It records the Single Send ID and temporary
suppression as cleanup requirements.

The harness never auto-removes the suppression because removal restores
deliverability. Running the proof and later cleaning it up are separate
provider mutations requiring explicit approval.
