from __future__ import annotations

import json

from public_presence_os.meta_calibration_pack import (
    MANIFEST_SCHEMA,
    PACK_SCHEMA,
    build_calibration_pack,
    build_encrypted_manifest,
)
from public_presence_os.meta_live_runtime import MetaEventStore
from public_presence_os.meta_read_only_report import build_read_only_report
from public_presence_os.meta_shadow_runtime import (
    EVALUATION_FIELDS,
    MetaShadowStore,
    compile_shadow_decision,
    run_shadow,
)
from public_presence_os.pilot_growth_operations import load_policy


def seed(store, object_type, external_id, text):
    key = "message" if object_type in {"POST", "COMMENT"} else "text"
    return store.ingest(
        platform="THREADS" if object_type == "REPLY" else "FACEBOOK_PAGE",
        object_type=object_type,
        external_id=external_id,
        observed_at="2026-10-03T00:00:00Z",
        fetched_at="2026-10-03T00:01:00Z",
        provenance="OFFICIAL_META_API",
        normalized={"id": external_id, key: text},
        cursor="cursor-1",
    )


def test_real_read_only_events_reuse_cp92_and_never_gain_write_authority(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    receipt = seed(events, "REPLY", "reply-1", "Can you clarify the source?")
    event = events.events()[0]
    decision = compile_shadow_decision(event, policy=load_policy())
    assert decision.evidence_event_id == receipt.event_id
    assert decision.action == "REPLY"
    assert decision.risk_class == "AMBER"
    assert decision.external_write_allowed is False
    assert decision.social_api_call_allowed is False
    assert decision.kill_switch_required is True


def test_shadow_run_is_idempotent_and_reports_unmet_real_sample(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    seed(events, "COMMENT", "comment-1", "Nice")
    seed(events, "COMMENT", "comment-2", "Where is the source?")
    decisions = MetaShadowStore(tmp_path / "shadow.sqlite3")
    first = run_shadow(events, decisions)
    second = run_shadow(events, decisions)
    assert first["sample_size"] == 2
    assert first["accepted_this_run"] == 2
    assert first["actions"] == {"IGNORE": 1, "REPLY": 1}
    assert first["risk_classes"] == {"AMBER": 1, "GREEN": 1}
    assert first["sample_target_met"] is False
    assert first["calibration_complete"] is False
    assert first["external_write_count"] == 0
    assert second["accepted_this_run"] == 0
    assert second["duplicates_this_run"] == 2


def test_calibration_requires_all_fields_and_does_not_lower_sample_gate(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    seed(events, "POST", "post-1", "A useful public update")
    shadow = MetaShadowStore(tmp_path / "shadow.sqlite3")
    run_shadow(events, shadow)
    decision = compile_shadow_decision(events.events()[0], policy=load_policy())
    shadow.evaluate(decision.decision_id, {field: "PASS" for field in EVALUATION_FIELDS})
    report = shadow.report()
    assert report["evaluated_count"] == 1
    assert report["sample_target_met"] is False
    assert report["calibration_complete"] is False


def test_calibration_band_is_pending_green_amber_then_red(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    for index in range(100):
        seed(events, "POST", f"post-{index}", f"Useful public update {index}")

    shadow = MetaShadowStore(tmp_path / "shadow.sqlite3")
    run_shadow(events, shadow)
    decisions = [
        compile_shadow_decision(event, policy=load_policy())
        for event in events.events()
    ]

    assert shadow.report()["calibration_band"] == "PENDING"

    all_pass = {field: "PASS" for field in EVALUATION_FIELDS}
    for decision in decisions:
        shadow.evaluate(decision.decision_id, all_pass)
    green = shadow.report()
    assert green["calibration_complete"] is True
    assert green["calibration_band"] == "GREEN"
    assert green["live_authority"] == "NONE"
    assert green["external_write_count"] == 0

    amber_values = dict(all_pass)
    amber_values["voice"] = "FAIL"
    shadow.evaluate(decisions[0].decision_id, amber_values)
    assert shadow.report()["calibration_band"] == "AMBER"

    red_values = dict(all_pass)
    red_values["factual_grounding"] = "FAIL"
    shadow.evaluate(decisions[1].decision_id, red_values)
    red = shadow.report()
    assert red["calibration_band"] == "RED"
    assert red["live_authority"] == "NONE"
    assert red["external_write_count"] == 0


def test_secret_free_read_only_report_binds_event_log_and_shadow_replay(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    seed(events, "COMMENT", "comment-1", "Where is the source?")
    seed(events, "COMMENT", "comment-2", "Nice")
    events.start_sync("run-1", started_at="2026-10-21T10:00:00Z")
    events.finish_sync(
        "run-1",
        completed_at="2026-10-21T10:00:01Z",
        state="READ_ONLY_REAL_SYNC_PASS",
        accepted_count=2,
        duplicate_count=0,
    )
    events.start_sync("run-2", started_at="2026-10-21T10:01:00Z")
    events.finish_sync(
        "run-2",
        completed_at="2026-10-21T10:01:01Z",
        state="READ_ONLY_REAL_SYNC_PASS",
        accepted_count=0,
        duplicate_count=2,
    )
    shadow = MetaShadowStore(tmp_path / "shadow.sqlite3")
    first = run_shadow(events, shadow)
    replay = run_shadow(events, shadow)

    report = build_read_only_report(
        events_db=events.path,
        shadow_db=shadow.path,
        sync_replay={
            "state": "READ_ONLY_REAL_SYNC_PASS",
            "identities": {
                "FACEBOOK_PAGE": "2816314015107071",
                "INSTAGRAM_PROFESSIONAL": "17841429701593250",
            },
            "accepted": 0,
            "duplicates": 2,
            "holds": [],
            "write_count": 0,
        },
        shadow_first=first,
        shadow_replay=replay,
        source_sha="a" * 40,
        material_change_reference="owner-confirmed-rotation-2026-10-21",
    )

    assert report["event_log"]["health"] == "PASS"
    assert report["event_log"]["event_count"] == 2
    assert report["event_log"]["duplicate_observations"] == 2
    assert report["shadow"]["idempotency"] == "PASS"
    assert report["shadow"]["replay_duplicates"] == 2
    assert report["safety"]["social_write_count"] == 0
    assert report["safety"]["threads"] == "HOLD_EXTERNAL"
    assert "owner-confirmed" not in json.dumps(report)


def test_calibration_pack_minimizes_public_content_and_omits_direct_ids(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    fake_token = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
    seed(
        events,
        "COMMENT",
        "sensitive-external-id",
        "Contact test@example.com or @person at +40 712 345 678. "
        f"See https://example.com/private and token {fake_token}.",
    )
    shadow = MetaShadowStore(tmp_path / "shadow.sqlite3")
    run_shadow(events, shadow)

    pack = build_calibration_pack(
        events_db=events.path,
        shadow_db=shadow.path,
        source_sha="a" * 40,
    )

    assert pack["schema"] == PACK_SCHEMA
    assert pack["item_count"] == 1
    serialized = json.dumps(pack)
    assert "sensitive-external-id" not in serialized
    assert "test@example.com" not in serialized
    assert "@person" not in serialized
    assert "+40 712 345 678" not in serialized
    assert "https://example.com/private" not in serialized
    assert fake_token not in serialized
    assert pack["items"][0]["ratings"] == {
        field: "UNKNOWN" for field in EVALUATION_FIELDS
    }
    assert pack["safety"]["external_write_count"] == 0


def test_encrypted_manifest_contains_only_ciphertext_metadata(tmp_path):
    events = MetaEventStore(tmp_path / "events.sqlite3")
    seed(events, "COMMENT", "comment-1", "Where is the source?")
    shadow = MetaShadowStore(tmp_path / "shadow.sqlite3")
    run_shadow(events, shadow)
    pack = build_calibration_pack(
        events_db=events.path,
        shadow_db=shadow.path,
        source_sha="b" * 40,
    )
    plaintext = tmp_path / "pack.json"
    plaintext.write_text(json.dumps(pack), encoding="utf-8")
    ciphertext = tmp_path / "pack.cms"
    ciphertext.write_bytes(b"encrypted bytes only")
    certificate = tmp_path / "recipient.crt"
    certificate.write_text("public certificate", encoding="utf-8")

    manifest = build_encrypted_manifest(
        plaintext_pack=plaintext,
        ciphertext=ciphertext,
        recipient_certificate=certificate,
    )

    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["calibration_set_id"] == pack["calibration_set_id"]
    assert manifest["plaintext_persisted"] is False
    assert manifest["secrets_included"] is False
    assert "Where is the source?" not in json.dumps(manifest)
