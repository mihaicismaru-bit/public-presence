from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .control import canonical_json
from .meta_live_runtime import EXPECTED_IG_ID, EXPECTED_PAGE_ID, MetaEventStore
from .meta_shadow_runtime import MetaShadowStore


REPORT_SCHEMA = "PPOS_READ_ONLY_REAL_REPORT_V1"


class MetaReadOnlyReportHold(ValueError):
    pass


def _json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_INPUT_NOT_OBJECT")
    return value


def _require_exact_identities(sync: Mapping[str, Any]) -> None:
    identities = sync.get("identities")
    if identities != {
        "FACEBOOK_PAGE": EXPECTED_PAGE_ID,
        "INSTAGRAM_PROFESSIONAL": EXPECTED_IG_ID,
    }:
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_IDENTITY_MISMATCH")


def build_read_only_report(
    *,
    events_db: str | Path,
    shadow_db: str | Path,
    sync_replay: Mapping[str, Any],
    shadow_first: Mapping[str, Any],
    shadow_replay: Mapping[str, Any],
    source_sha: str,
    material_change_reference: str,
) -> dict[str, Any]:
    if sync_replay.get("state") != "READ_ONLY_REAL_SYNC_PASS":
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_SYNC_NOT_PASS")
    if sync_replay.get("write_count") != 0 or sync_replay.get("holds") != []:
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_SYNC_WRITE_OR_HOLD")
    _require_exact_identities(sync_replay)

    event_store = MetaEventStore(events_db)
    events = event_store.events()
    event_ids = [str(event["event_id"]) for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_EVENT_ID_DUPLICATE")
    sync_runs = event_store.sync_runs()
    if len(sync_runs) < 2 or any(run["state"] != "READ_ONLY_REAL_SYNC_PASS" for run in sync_runs):
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_EVENT_LOG_RUN_STATE")

    shadow_report = MetaShadowStore(shadow_db).report()
    sample_size = int(shadow_report["sample_size"])
    if shadow_first.get("accepted_this_run") != sample_size:
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_SHADOW_FIRST_PASS")
    if (
        shadow_replay.get("accepted_this_run") != 0
        or shadow_replay.get("duplicates_this_run") != sample_size
    ):
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_SHADOW_REPLAY_NOT_IDEMPOTENT")
    if shadow_report.get("external_write_count") != 0 or shadow_report.get("live_authority") != "NONE":
        raise MetaReadOnlyReportHold("HOLD_READ_ONLY_REPORT_SHADOW_AUTHORITY")

    duplicate_count = sum(int(run["duplicate_count"]) for run in sync_runs)
    accepted_count = sum(int(run["accepted_count"]) for run in sync_runs)
    return {
        "schema": REPORT_SCHEMA,
        "source_sha": source_sha,
        "material_change_reference_sha256": sha256(
            material_change_reference.encode("utf-8")
        ).hexdigest(),
        "safety": {
            "kill_switch_engaged": True,
            "live_write": False,
            "write_capabilities": "LOCKED",
            "social_write_count": 0,
            "threads": "HOLD_EXTERNAL",
            "threads_enabled": False,
        },
        "identities": {
            "facebook_page_id": EXPECTED_PAGE_ID,
            "instagram_professional_id": EXPECTED_IG_ID,
        },
        "event_log": {
            "health": "PASS" if event_store.healthcheck() else "FAIL",
            "event_count": len(events),
            "event_ids_sha256": sha256(canonical_json(sorted(event_ids)).encode("utf-8")).hexdigest(),
            "sync_run_count": len(sync_runs),
            "accepted_observations": accepted_count,
            "duplicate_observations": duplicate_count,
            "replay_write_count": 0,
        },
        "shadow": {
            **shadow_report,
            "first_materialized": shadow_first.get("accepted_this_run"),
            "replay_duplicates": shadow_replay.get("duplicates_this_run"),
            "idempotency": "PASS",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-db", required=True)
    parser.add_argument("--shadow-db", required=True)
    parser.add_argument("--sync-replay", required=True)
    parser.add_argument("--shadow-first", required=True)
    parser.add_argument("--shadow-replay", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--material-change-reference", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = build_read_only_report(
        events_db=args.events_db,
        shadow_db=args.shadow_db,
        sync_replay=_json(args.sync_replay),
        shadow_first=_json(args.shadow_first),
        shadow_replay=_json(args.shadow_replay),
        source_sha=args.source_sha,
        material_change_reference=args.material_change_reference,
    )
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
