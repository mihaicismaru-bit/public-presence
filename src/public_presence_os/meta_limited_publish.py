from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .control import canonical_json
from .meta_write_control import (
    ControlledWriteIntent,
    ControlledWriter,
    ExternalWriteResult,
    MetaWriteHold,
    WriteLedger,
    validate_authority_receipt,
)


SCHEMA_VERSION = "PPOS_LIMITED_WRITE_POST_V1"
ACTION_CLASS = "PUBLISH_APPROVED_SINGLE_IMAGE_POST"
EXPECTED_PAGE_ID = "2816314015107071"
EXPECTED_IG_ID = "17841429701593250"
EXPECTED_PLATFORMS = ("FACEBOOK_PAGE", "INSTAGRAM_PROFESSIONAL")
GRAPH_HOST = "graph.facebook.com"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class LimitedWriteHold(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise LimitedWriteHold(f"HOLD_LIMITED_WRITE_BOOLEAN_{name}")


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_MANIFEST_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_MANIFEST_NOT_OBJECT")
    return value


def payload_body(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest.get("schema_version"),
        "post_id": manifest.get("post_id"),
        "caption": manifest.get("caption"),
        "alt_text": manifest.get("alt_text"),
        "asset_path": manifest.get("asset_path"),
        "asset_sha256": manifest.get("asset_sha256"),
        "rights_evidence_sha256": _hash(manifest.get("rights_evidence")),
        "platforms": manifest.get("platforms"),
        "targets": manifest.get("targets"),
        "threads_enabled": manifest.get("threads_enabled"),
    }


def payload_sha256(manifest: Mapping[str, Any]) -> str:
    return _hash(payload_body(manifest))


def validate_manifest(manifest: Mapping[str, Any], *, root: str | Path) -> dict[str, Any]:
    required = {
        "schema_version", "post_id", "caption", "alt_text", "asset_path", "asset_sha256",
        "rights_evidence", "platforms", "targets", "threads_enabled", "approval",
    }
    if set(manifest) != required or manifest.get("schema_version") != SCHEMA_VERSION:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_MANIFEST_SHAPE")
    if not isinstance(manifest.get("post_id"), str) or not manifest["post_id"].startswith("ppos-"):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_POST_ID")
    caption = manifest.get("caption")
    alt_text = manifest.get("alt_text")
    if not isinstance(caption, str) or not caption.strip() or len(caption.encode("utf-8")) > 2200:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_CAPTION")
    if not isinstance(alt_text, str) or not alt_text.strip() or len(alt_text.encode("utf-8")) > 1000:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_ALT_TEXT")
    if tuple(manifest.get("platforms") or ()) != EXPECTED_PLATFORMS:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_PLATFORM_SCOPE")
    if manifest.get("threads_enabled") is not False:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_THREADS_NOT_HELD")
    targets = manifest.get("targets")
    if targets != {"facebook_page_id": EXPECTED_PAGE_ID, "instagram_user_id": EXPECTED_IG_ID}:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_TARGET_DRIFT")
    rights = manifest.get("rights_evidence")
    if not isinstance(rights, dict) or rights.get("original_or_licensed") is not True:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_RIGHTS")
    asset_path = manifest.get("asset_path")
    if not isinstance(asset_path, str) or not asset_path.startswith("content/limited-write/"):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_ASSET_PATH")
    path = Path(root) / asset_path
    try:
        actual_asset_hash = sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_ASSET_MISSING") from exc
    if not HEX64.fullmatch(str(manifest.get("asset_sha256"))) or actual_asset_hash != manifest["asset_sha256"]:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_ASSET_HASH")
    approval = manifest.get("approval")
    if not isinstance(approval, dict) or set(approval) != {
        "state", "content_commit_sha", "payload_sha256", "authority_receipt"
    }:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_APPROVAL_SHAPE")
    state = approval.get("state")
    if state not in {"PENDING_OWNER_APPROVAL", "APPROVED"}:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_APPROVAL_STATE")
    digest = payload_sha256(manifest)
    if state == "APPROVED":
        if approval.get("payload_sha256") != digest:
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_APPROVAL_PAYLOAD_DRIFT")
        if not HEX40.fullmatch(str(approval.get("content_commit_sha"))):
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_CONTENT_COMMIT")
        if not isinstance(approval.get("authority_receipt"), dict):
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_AUTHORITY_RECEIPT")
    elif any(approval.get(key) is not None for key in ("content_commit_sha", "payload_sha256", "authority_receipt")):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_PENDING_MUST_BE_EMPTY")
    return {
        "state": state,
        "post_id": manifest["post_id"],
        "payload_sha256": digest,
        "asset_sha256": actual_asset_hash,
        "platforms": list(EXPECTED_PLATFORMS),
        "threads_enabled": False,
    }


def _graph_hold(exc: HTTPError) -> str:
    reason = f"HOLD_LIMITED_WRITE_HTTP_{exc.code}"
    try:
        body = exc.read(64 * 1024 + 1)
        payload = json.loads(body.decode("utf-8")) if len(body) <= 64 * 1024 else {}
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code, subcode = error.get("code"), error.get("error_subcode")
        if type(code) is int:
            reason += f"_GRAPH_{code}"
        if type(subcode) is int and subcode:
            reason += f"_SUB_{subcode}"
    return reason


class GraphClient:
    def __init__(self, *, token: str, timeout_seconds: float = 30.0, opener=None) -> None:
        if not isinstance(token, str) or len(token.strip()) < 20 or any(ch.isspace() for ch in token):
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_TOKEN_MISSING")
        self._token = token
        self._timeout = timeout_seconds
        self._opener = opener or build_opener()

    def request(self, method: str, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        if method not in {"GET", "POST"} or not path.startswith("/") or "?" in path:
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_REQUEST")
        encoded = urlencode(sorted((str(k), str(v)) for k, v in params.items()))
        url = f"https://{GRAPH_HOST}{path}"
        data = None
        if method == "GET":
            url += "?" + encoded
        else:
            data = encoded.encode("utf-8")
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "PUBLIC-PRESENCE-OS/limited-write-v1",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as exc:
            raise LimitedWriteHold(_graph_hold(exc)) from None
        except (URLError, TimeoutError):
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_AMBIGUOUS_TRANSPORT") from None
        if len(body) > 8 * 1024 * 1024:
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_RESPONSE_TOO_LARGE")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_RESPONSE_INVALID") from None
        if not isinstance(value, dict):
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_RESPONSE_NOT_OBJECT")
        return value


@dataclass
class PlatformTransport:
    client: GraphClient
    platform: str
    graph_version: str
    destination_id: str
    caption: str
    asset_url: str
    sleeper: Any = time.sleep

    def _recent(self) -> list[dict[str, Any]]:
        if self.platform == "FACEBOOK_PAGE":
            path = f"/{self.graph_version}/{self.destination_id}/published_posts"
            fields = "id,message,created_time,permalink_url"
        else:
            path = f"/{self.graph_version}/{self.destination_id}/media"
            fields = "id,caption,media_type,permalink,timestamp"
        payload = self.client.request("GET", path, {"fields": fields, "limit": "100"})
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def find_existing(self) -> dict[str, Any] | None:
        field = "message" if self.platform == "FACEBOOK_PAGE" else "caption"
        matches = [item for item in self._recent() if item.get(field) == self.caption]
        if len(matches) > 1:
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_DUPLICATE_LIVE_POSTS")
        return matches[0] if matches else None

    def execute(self, _intent: ControlledWriteIntent) -> ExternalWriteResult:
        existing = self.find_existing()
        if existing:
            return ExternalWriteResult(
                str(existing["id"]),
                {"state": "DUPLICATE_NOOP", "platform": self.platform, "external_id": str(existing["id"])},
            )
        try:
            if self.platform == "FACEBOOK_PAGE":
                payload = self.client.request(
                    "POST",
                    f"/{self.graph_version}/{self.destination_id}/photos",
                    {"url": self.asset_url, "caption": self.caption, "published": "true"},
                )
                external_id = payload.get("post_id") or payload.get("id")
            else:
                container = self.client.request(
                    "POST",
                    f"/{self.graph_version}/{self.destination_id}/media",
                    {"image_url": self.asset_url, "caption": self.caption},
                ).get("id")
                if not container:
                    raise LimitedWriteHold("HOLD_LIMITED_WRITE_IG_CONTAINER_ID")
                for _ in range(20):
                    status = self.client.request(
                        "GET", f"/{self.graph_version}/{container}", {"fields": "status_code,status"}
                    )
                    if status.get("status_code") == "FINISHED":
                        break
                    if status.get("status_code") in {"ERROR", "EXPIRED"}:
                        raise LimitedWriteHold("HOLD_LIMITED_WRITE_IG_CONTAINER_FAILED")
                    self.sleeper(3)
                else:
                    raise LimitedWriteHold("HOLD_LIMITED_WRITE_IG_CONTAINER_TIMEOUT")
                external_id = self.client.request(
                    "POST",
                    f"/{self.graph_version}/{self.destination_id}/media_publish",
                    {"creation_id": str(container)},
                ).get("id")
        except LimitedWriteHold as exc:
            if exc.reason == "HOLD_LIMITED_WRITE_AMBIGUOUS_TRANSPORT":
                existing = self.find_existing()
                if existing:
                    return ExternalWriteResult(
                        str(existing["id"]),
                        {"state": "AMBIGUOUS_RESOLVED_BY_READBACK", "platform": self.platform,
                         "external_id": str(existing["id"])},
                    )
            raise
        if not external_id:
            raise LimitedWriteHold("HOLD_LIMITED_WRITE_EXTERNAL_ID")
        return ExternalWriteResult(
            str(external_id),
            {"state": "API_ACCEPTED", "platform": self.platform, "external_id": str(external_id)},
        )

    def readback(self, external_id: str) -> bool:
        fields = "id,message,permalink_url" if self.platform == "FACEBOOK_PAGE" else "id,caption,permalink,media_type"
        payload = self.client.request(
            "GET", f"/{self.graph_version}/{external_id}", {"fields": fields}
        )
        text_field = "message" if self.platform == "FACEBOOK_PAGE" else "caption"
        return str(payload.get("id")) == external_id and payload.get(text_field) == self.caption


def execute_manifest(
    manifest: Mapping[str, Any], *, root: str | Path, ledger_path: str | Path,
    asset_url: str, now_utc: str | None = None,
) -> dict[str, Any]:
    summary = validate_manifest(manifest, root=root)
    approval = manifest["approval"]
    if approval["state"] != "APPROVED":
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_OWNER_APPROVAL_REQUIRED")
    if _bool_env("META_THREADS_ENABLED", default=True):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_THREADS_NOT_HELD")
    if _bool_env("KILL_SWITCH", default=True) or not _bool_env("LIVE_WRITE", default=False):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_KILL_SWITCH_OR_LIVE_WRITE")
    if os.environ.get("META_PAGE_ID") != EXPECTED_PAGE_ID or os.environ.get("META_IG_USER_ID") != EXPECTED_IG_ID:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_RUNTIME_TARGET_DRIFT")
    if os.environ.get("OWNER_APPROVAL_ID") != approval["authority_receipt"].get("receipt_id"):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_APPROVAL_ID_MISMATCH")
    if os.environ.get("APPROVED_PAYLOAD_SHA256") != summary["payload_sha256"]:
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_DISPATCH_HASH_MISMATCH")
    if not asset_url.startswith(
        f"https://raw.githubusercontent.com/mihaicismaru-bit/public-presence/{approval['content_commit_sha']}/"
    ):
        raise LimitedWriteHold("HOLD_LIMITED_WRITE_ASSET_URL")
    now = now_utc or _utc_now()
    authority = validate_authority_receipt(approval["authority_receipt"], now_utc=now)
    policy = json.loads((Path(root) / "config/meta_action_risk_policy.json").read_text(encoding="utf-8"))
    green = tuple(policy.get("external_green_actions") or ())
    token = os.environ.get("META_PAGE_ACCESS_TOKEN", "")
    client = GraphClient(token=token)
    graph_version = os.environ.get("META_GRAPH_API_VERSION", "v26.0")
    receipts = []
    rights_hash = _hash(manifest["rights_evidence"])
    payload_hash = summary["payload_sha256"]
    targets = {
        "FACEBOOK_PAGE": EXPECTED_PAGE_ID,
        "INSTAGRAM_PROFESSIONAL": EXPECTED_IG_ID,
    }
    for platform in EXPECTED_PLATFORMS:
        transport = PlatformTransport(
            client=client, platform=platform, graph_version=graph_version,
            destination_id=targets[platform], caption=manifest["caption"], asset_url=asset_url,
        )
        writer = ControlledWriter(
            ledger=WriteLedger(ledger_path), transport=transport, readback=transport.readback,
            authority=authority, external_green_actions=green,
            kill_switch_engaged=lambda: _bool_env("KILL_SWITCH", default=True),
            live_write_enabled=lambda: _bool_env("LIVE_WRITE", default=False),
        )
        intent = ControlledWriteIntent(
            idempotency_key=f"{manifest['post_id']}:{platform}:{payload_hash[:16]}",
            action_class=ACTION_CLASS, platform=platform, payload_sha256=payload_hash,
            rights_evidence_sha256=rights_hash, approval_id=authority.receipt_id,
            approval_expires_at=authority.expires_at,
        )
        try:
            result = writer.execute(intent, now_utc=now)
        except (MetaWriteHold, LimitedWriteHold) as exc:
            reason = getattr(exc, "reason", "HOLD_LIMITED_WRITE_UNCLASSIFIED")
            raise LimitedWriteHold(reason) from None
        receipts.append({"platform": platform, **result})
    return {
        "schema_version": "PPOS_LIMITED_WRITE_RECEIPT_V1",
        "post_id": manifest["post_id"],
        "payload_sha256": payload_hash,
        "asset_sha256": manifest["asset_sha256"],
        "executed_at": now,
        "threads_enabled": False,
        "writes": receipts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--root", default=".")
    execute = sub.add_parser("execute")
    execute.add_argument("--manifest", required=True)
    execute.add_argument("--root", default=".")
    execute.add_argument("--ledger", required=True)
    execute.add_argument("--asset-url", required=True)
    execute.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.command == "validate":
        print(canonical_json(validate_manifest(manifest, root=args.root)))
        return 0
    try:
        receipt = execute_manifest(
            manifest, root=args.root, ledger_path=args.ledger, asset_url=args.asset_url,
        )
    except LimitedWriteHold as exc:
        receipt = {
            "schema_version": "PPOS_LIMITED_WRITE_HOLD_V1",
            "post_id": manifest.get("post_id", "UNKNOWN"),
            "payload_sha256": payload_sha256(manifest),
            "state": "HOLD",
            "reason": exc.reason,
            "executed_at": _utc_now(),
        }
        Path(args.receipt).write_text(canonical_json(receipt) + "\n", encoding="utf-8")
        print(canonical_json(receipt))
        return 2
    Path(args.receipt).write_text(canonical_json(receipt) + "\n", encoding="utf-8")
    print(canonical_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
