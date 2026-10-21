# PUBLIC PRESENCE OS — TEXT/PHOTO

Standalone canonical repository: https://github.com/mihaicismaru-bit/public-presence

The Python distribution and CLI retain the public-presence-os name for compatibility. Run installation, tests and build commands from this repository root.

```sh
python -m pip install -e . pytest
PYTHONPATH=src python -m pytest -q
```

Migration provenance, branch reconciliation and manual environment setup: [migration guide](docs/REPOSITORY_MIGRATION_2026-10-12.md).

## Authority split
- **GitHub**: executable source, schemas/config, tests and CI.
- **Google Drive**: checkpoints, evidence, decisions, changelog and rollback evidence.

## Safety posture
Ready for credential-gated `READ_ONLY_REAL`. The current pilot activates Facebook Page and Instagram Professional. Threads remains disabled (`META_THREADS_ENABLED=false`) and `HOLD_EXTERNAL`. LinkedIn remains API-gated, X excluded while paid, Bluesky HOLD_ROI. The live read runtime is GET-only and requires the global kill switch to remain engaged with `LIVE_WRITE=OFF`. No network publishing, scheduler write, queue mutation, deploy, paid-service dependency, external GREEN action, or owner write authority is enabled.

## Validate
`PYTHONPATH=src python -m public_presence_os.cli validate --root .`

Secret-safe Meta preflight (prints presence/status only):

`PYTHONPATH=src python -m public_presence_os.cli meta-preflight --db var/meta-events.sqlite3`

After credentials are locally provisioned, explicitly add `--live` to perform the bounded read-only identity/content probe. Real tokens must never be passed on the command line or committed.

Read-only sync and shadow decision materialization:

`PYTHONPATH=src python -m public_presence_os.cli meta-sync --db var/meta-events.sqlite3`

`PYTHONPATH=src python -m public_presence_os.cli meta-shadow --events-db var/meta-events.sqlite3 --shadow-db var/meta-shadow.sqlite3`

GitHub provides two manual-only workflows bound to the `public-presence-shadow`
environment. `PUBLIC PRESENCE SHADOW PREFLIGHT` performs the bounded identity
probe. After that gate passes, `PUBLIC PRESENCE READ ONLY REAL` performs a
read-only replay, materializes shadow decisions twice to prove idempotency, and
uploads a secret-free summary report. It also creates a seven-day calibration
artifact containing only an encrypted, identifier-minimized review pack and a
secret-free manifest. The repository contains only the recipient certificate;
the private decryption key must remain outside Git and GitHub. Both workflows keep
`KILL_SWITCH=true`, `LIVE_WRITE=false`, and `META_THREADS_ENABLED=false`.

The manual-only `PUBLIC PRESENCE LIMITED WRITE` workflow is the controlled
exception for an exact owner-approved post. It defaults to validation with the
kill switch engaged. Execution is admitted only when the committed asset,
caption, destination ids, content hash, time-bounded owner receipt, explicit
dispatch hash and confirmation phrase all match. Before either platform write
it performs a live exact-caption dedup read; after a write it requires live
readback and persists a secret-free receipt. Threads remains disabled. The
workflow never publishes an unapproved manifest and never retries an ambiguous
write.

Decrypt an authorized calibration artifact locally after download:

```sh
openssl cms -decrypt -inform DER \
  -in shadow-calibration-pack.cms \
  -recip config/calibration_artifact_recipient.crt \
  -inkey /secure/local/path/calibration-private.pem \
  -out shadow-calibration-pack.json
```

## Reproducible package
`PYTHONPATH=src python scripts/build_release.py`

The build creates a deterministic ZIP from source/config/tests/docs/CI inputs. It is not a deploy artifact and contains no secrets or account credentials.
