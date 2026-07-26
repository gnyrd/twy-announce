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

Each deliverable row has exactly four fields: normalized `email`,
`custom_fields`, `proposed_lists`, and `reasons`. The dry-run producer and
production writer both test this exact schema and fail closed on drift.

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
2. Resolve or create the migration-time unsubscribe group.
3. Re-read that group by ID and require exact name, description, and default
   state.
4. Add the approved marketing suppressions, then check their group-specific
   membership up to 15 times at one-second intervals to tolerate SendGrid
   read-after-write lag. Continue only after every approved suppression is
   visible.
5. Write the local cleaned denylist once, privately and atomically.
6. Upsert only deliverable contacts and wait for every async job.
7. Re-read every deliverable contact and verify its expected list membership.
8. Seal a private apply report.

The writer has no global-suppression, mail-send, deletion, campaign-scheduling,
or Mailchimp endpoint. This matters because SendGrid documents that adding
addresses to a deleted or nonexistent unsubscribe group can place them on the
global suppression list. The writer always verifies the exact group by ID
before adding group-specific suppressions.

The production naming cutover renamed the only production unsubscribe group
to `Email: Unsubscribed`. SendGrid still requires each send to identify the
enforcing group with `asm.group_id` or the equivalent Single Send field. The
default flag controls which group appears on the recipient preferences page
when `groups_to_display` is omitted. It does not make an untagged send honor
this group automatically.

`first_name` and `last_name` use SendGrid reserved contact fields.
`twy_status` and `twy_role` resolve to exact custom-field IDs at apply time.
Unsubscribed, cleaned, and archived records never become Marketing Contacts.
Cleaned/bounced state is deliberately not converted into an unsubscribe-group
suppression. It is retained in the private local cleaned denylist instead.
Every future TWY contact importer must read that denylist before constructing
provider writes.

Any partial apply remains incomplete. The writer does not delete or roll back
provider objects automatically. A retry requires a fresh read-only
reconciliation, a distinct apply ID, and a new approval. Any apply ID with
existing evidence, complete or partial, is immutable and cannot be reused.

The production contact apply and provider naming cutover are complete. The
one-time migration writer is retained only as historical, digest-locked
evidence. It is not a recurring contact synchronization path.

## Suppression enforcement harness

`src/sendgrid_suppression_test.py` is a separately approval-gated test harness.
The proof runner has no CLI and has not been run against SendGrid. The file's
only CLI action is the separately approved cleanup of a completed proof.
Both proof creation and cleanup independently enforce the same two-address
recipient allowlist before any provider access.

It accepts only `admin@tiffanywoodyoga.com` or `jpgan6@gmail.com`, with
`jpgan6@gmail.com` as the preferred proof recipient. The admin mailbox remains
available only for an explicitly selected proof. The harness also requires an
isolated list containing exactly that one address, the exact
`Email: Unsubscribed` group, and the statement
`APPROVE TWY SENDGRID SUPPRESSION ENFORCEMENT TEST`.

The harness adds and verifies the temporary group suppression before creating a
Single Send tagged with the same group. It passes only when SendGrid reports
one request, zero deliveries, zero unique opens, zero unique clicks, and the
suppression still present. Stats are requested beginning with the previous UTC
date so a run near midnight cannot fall outside the query window. The exact
`requests == 1` result is a fail-closed live-provider assumption that must be
confirmed during the first separately approved run.

The completed proof records the Single Send ID, temporary suppression, and an
immutable `cleanup-plan.json`. The proof never auto-removes the suppression
because removal restores deliverability. Running the proof and cleaning it up
are separate provider mutations requiring separate approvals.

Cleanup requires the exact statement
`APPROVE TWY SENDGRID SUPPRESSION TEST CLEANUP`, the proof digest, cleanup
digest, target account, recipient, and an approval window of no more than 24
hours. The cleanup command verifies the account, exact group, and current
suppression membership before deleting only that group membership. It then
re-reads the group and seals evidence only if the recipient is absent:

```bash
PYTHONPATH=src python3 src/sendgrid_suppression_test.py cleanup \
  --proof-plan /private/path/proof-plan.json \
  --proof-evidence-dir /root/twy/data/sendgrid_proofs/PROOF_RUN_ID \
  --approval-file /private/path/cleanup-approval.json \
  --expected-cleanup-digest CLEANUP_OPERATION_DIGEST \
  --cleanup-id cleanup_YYYYMMDDTHHMMSSZ
```

This command is implemented and tested against fakes but has not been invoked
against SendGrid. The source safety scan that excludes global suppression,
deletion, and mail-send endpoints applies to the production contact writer.
The suppression harness is intentionally Single-Send-capable and remains
separately approval-gated.
