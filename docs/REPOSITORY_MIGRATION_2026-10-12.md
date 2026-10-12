# PUBLIC PRESENCE OS standalone migration — 2026-10-12

Canonical source: https://github.com/mihaicismaru-bit/public-presence

## Scope and provenance

All 301 files from the current PPOS source/workflow selection were extracted from CIVORA. The subproject is now the repository root. The stale nested CI copy was replaced by the effective root CI. No other CIVORA product or credential was imported. Author, timestamp, message and parent relationships of relevant commits are preserved through a path-filtered Git history; hashes change because trees change. The complete old-to-new commit map is in [commit-map.txt](migration/commit-map.txt). Source snapshots: CIVORA main 424cb4e7ec6860998bdfb9cb3b2a5f7829899426 and subsequent full-clone main 871996e9025c3584dcfee60babcaf8ab31e15bab have the same current PPOS tree. Filtered PPOS baseline: 22b147df5e (see Git history for full SHA).

Package name, Python namespace, CLI and deterministic release ZIP name retain `public-presence-os` for compatibility. All install/build/test commands execute from `.`. GitHub CI evaluates every main push and pull request, including workflow-only changes. The root shadow workflow remains manual-only.

Historical links to CIVORA PRs and historical evidence SHAs identify original evidence; they intentionally continue to point at that evidence. Current operator instructions target the new repository. CP58 registry and CP92 offline closure are preserved; migration does not grant live authority or claim a completed real shadow pilot.

## PR and branch reconciliation

86 relevant historical/active PRs and 94 PPOS-named branches are inventoried in [PR reconciliation](migration/pr-reconciliation.json) and [branch reconciliation](migration/branch-reconciliation.json). Their filtered tips are retained as `archive/civora/pr-N` and `archive/civora/branch/NAME` tags. Archive tags are recovery/evidence references, not current runnable releases. Start any new work from standalone main and port distinct changes rather than replacing current main with an old tree.

- #1368 Graph 10 binding context and #1372 denied-stage diagnostics are in main.
- #1361 Page-token redaction assertions are ported into the later fail-closed binding regression.
- #1359 early secret/shadow CI guards are restored and adapted to the current two approved secret bindings and manual trigger contract.
- #1338, #1339 and #1342 have identical core implementation bytes in main; all tips remain archived.
- #1135 CP56 and #1117 CP39 are older alternate designs superseded by the implemented successor contracts. Their full filtered code remains in archive tags; merging their entire old trees would regress later controls.

## Manual environment and credentials

Create the `public-presence-shadow` environment in the new repository, restricted to main with the owner as required reviewer. Provision these two environment secrets directly from the private credential source, never through chat, source files, shell arguments, workflow inputs or reports:

- `META_PAGE_ACCESS_TOKEN`: selected Facebook Page read credential.
- `META_USER_ACCESS_TOKEN`: User credential needed to prove Page-to-Instagram binding through managed Pages.

The environment and secrets are not transferred by Git. No secret value was read or copied. The workflow needs no repository variables: app/Page/IG IDs and API-version pins remain explicit non-secret contract values. `.env.example` contains placeholders only. `META_APP_SECRET` is optional local provisioning metadata and is not bound in Actions. Do not provision a Threads credential during this migration.

Mandatory posture: `KILL_SWITCH=true`, `LIVE_WRITE=false`, `META_THREADS_ENABLED=false`, empty Threads credential. No scheduled or push-triggered Meta reads; no social writes. A future manual preflight needs a real confirmed material configuration-change reference. Offline CI success does not prove live credentials or Meta entitlements work.

## Cleanup and rollback

CIVORA removal belongs to a separate PR after standalone CI passes. Remove only `public-presence-os/`, its two root workflows and the corresponding README row. Keep other products and shared utilities. Original CIVORA history/PRs remain evidence. Roll back the cleanup by reverting its merge commit; do not delete or rewrite source history. Standalone rollback uses the preserved filtered baseline and archive tags; old archive workflows must not be dispatched.

No live Meta call, deployment, publication or write permission activation is part of this migration.
