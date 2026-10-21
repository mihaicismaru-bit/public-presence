from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from public_presence_os.meta_limited_publish import (
    LimitedWriteHold,
    PlatformTransport,
    SCHEMA_VERSION,
    load_manifest,
    payload_sha256,
    validate_manifest,
)
from public_presence_os.meta_write_control import ControlledWriteIntent


def _manifest(tmp_path: Path) -> tuple[dict, Path]:
    asset = tmp_path / "content/limited-write/test/hero.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"jpeg-fixture")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "post_id": "ppos-test-post",
        "caption": "A public caption",
        "alt_text": "Accessible description",
        "asset_path": "content/limited-write/test/hero.jpg",
        "asset_sha256": sha256(asset.read_bytes()).hexdigest(),
        "rights_evidence": {
            "source": "OPENAI_IMAGE_GENERATION",
            "original_or_licensed": True,
            "third_party_logo_present": False,
            "owner_review_required": True,
        },
        "platforms": ["FACEBOOK_PAGE", "INSTAGRAM_PROFESSIONAL"],
        "targets": {
            "facebook_page_id": "2816314015107071",
            "instagram_user_id": "17841429701593250",
        },
        "threads_enabled": False,
        "approval": {
            "state": "PENDING_OWNER_APPROVAL",
            "content_commit_sha": None,
            "payload_sha256": None,
            "authority_receipt": None,
        },
    }
    path = tmp_path / "post.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, path


def test_pending_manifest_is_hash_bound_and_secret_free(tmp_path):
    manifest, path = _manifest(tmp_path)
    assert load_manifest(path) == manifest
    result = validate_manifest(manifest, root=tmp_path)
    assert result["state"] == "PENDING_OWNER_APPROVAL"
    assert result["payload_sha256"] == payload_sha256(manifest)
    assert result["threads_enabled"] is False


def test_manifest_rejects_asset_drift_and_target_expansion(tmp_path):
    manifest, _ = _manifest(tmp_path)
    (tmp_path / manifest["asset_path"]).write_bytes(b"tampered")
    with pytest.raises(LimitedWriteHold, match="HOLD_LIMITED_WRITE_ASSET_HASH"):
        validate_manifest(manifest, root=tmp_path)
    manifest, _ = _manifest(tmp_path / "second")
    manifest["platforms"].append("THREADS")
    with pytest.raises(LimitedWriteHold, match="HOLD_LIMITED_WRITE_PLATFORM_SCOPE"):
        validate_manifest(manifest, root=tmp_path / "second")


def test_approved_manifest_must_bind_exact_payload_and_commit(tmp_path):
    manifest, _ = _manifest(tmp_path)
    manifest["approval"] = {
        "state": "APPROVED",
        "content_commit_sha": "a" * 40,
        "payload_sha256": "b" * 64,
        "authority_receipt": {"receipt_id": "owner-approval"},
    }
    with pytest.raises(LimitedWriteHold, match="HOLD_LIMITED_WRITE_APPROVAL_PAYLOAD_DRIFT"):
        validate_manifest(manifest, root=tmp_path)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, path, params):
        self.calls.append((method, path, params))
        return self.replies.pop(0)


def _intent(platform: str) -> ControlledWriteIntent:
    return ControlledWriteIntent(
        idempotency_key=f"idem:{platform}", action_class="PUBLISH_APPROVED_SINGLE_IMAGE_POST",
        platform=platform, payload_sha256="a" * 64, rights_evidence_sha256="b" * 64,
        approval_id="approval", approval_expires_at="2026-10-22T00:00:00Z",
    )


def test_live_caption_match_is_duplicate_noop_before_post():
    client = FakeClient([{"data": [{"id": "existing", "message": "caption"}]}])
    transport = PlatformTransport(
        client=client, platform="FACEBOOK_PAGE", graph_version="v26.0",
        destination_id="2816314015107071", caption="caption",
        asset_url="https://raw.githubusercontent.com/owner/repo/sha/hero.jpg",
    )
    result = transport.execute(_intent("FACEBOOK_PAGE"))
    assert result.external_id == "existing"
    assert result.receipt["state"] == "DUPLICATE_NOOP"
    assert [call[0] for call in client.calls] == ["GET"]


def test_facebook_photo_write_occurs_once_after_empty_dedup_read():
    client = FakeClient([{"data": []}, {"post_id": "2816314015107071_123"}])
    transport = PlatformTransport(
        client=client, platform="FACEBOOK_PAGE", graph_version="v26.0",
        destination_id="2816314015107071", caption="caption",
        asset_url="https://raw.githubusercontent.com/owner/repo/sha/hero.jpg",
    )
    result = transport.execute(_intent("FACEBOOK_PAGE"))
    assert result.external_id == "2816314015107071_123"
    assert [call[0] for call in client.calls] == ["GET", "POST"]
    assert client.calls[1][1].endswith("/photos")


def test_instagram_container_finishes_before_single_publish():
    client = FakeClient([
        {"data": []}, {"id": "container"}, {"status_code": "FINISHED"}, {"id": "ig-media"},
    ])
    transport = PlatformTransport(
        client=client, platform="INSTAGRAM_PROFESSIONAL", graph_version="v26.0",
        destination_id="17841429701593250", caption="caption",
        asset_url="https://raw.githubusercontent.com/owner/repo/sha/hero.jpg", sleeper=lambda _: None,
    )
    result = transport.execute(_intent("INSTAGRAM_PROFESSIONAL"))
    assert result.external_id == "ig-media"
    assert [call[0] for call in client.calls] == ["GET", "POST", "GET", "POST"]
    assert client.calls[-1][1].endswith("/media_publish")
