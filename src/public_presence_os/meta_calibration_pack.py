from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .control import canonical_json
from .meta_live_runtime import MetaEventStore
from .meta_shadow_runtime import EVALUATION_FIELDS, MetaShadowHold, MetaShadowStore


PACK_SCHEMA = "PPOS_META_SHADOW_CALIBRATION_PACK_V1"
MANIFEST_SCHEMA = "PPOS_META_SHADOW_CALIBRATION_ENCRYPTED_MANIFEST_V1"
MAX_PUBLIC_EXCERPT_CHARS = 280

_CREDENTIAL_LABEL = re.compile(
    r"(?i)\b(?:access[_ -]?token|api[_ -]?key|client[_ -]?secret|password|secret)\s*[:=]\s*\S+"
)
_KNOWN_TOKEN = re.compile(r"(?i)\b(?:sk|gh[pousr]|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b")
_LONG_OPAQUE = re.compile(r"\b[A-Za-z0-9_=/+.-]{32,}\b")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_HANDLE = re.compile(r"(?<!\w)@[A-Za-z0-9._-]{2,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")


def _public_text(normalized: Mapping[str, Any]) -> str:
    for key in ("message", "text", "caption"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "Observed public event"


def minimize_public_content(value: str) -> str:
    """Remove direct identifiers and opaque credentials, then bound the review excerpt."""
    text = " ".join(value.split())
    replacements = (
        (_CREDENTIAL_LABEL, "[redacted-credential]"),
        (_KNOWN_TOKEN, "[redacted-token]"),
        (_URL, "[redacted-url]"),
        (_EMAIL, "[redacted-email]"),
        (_HANDLE, "[redacted-handle]"),
        (_PHONE, "[redacted-phone]"),
        (_LONG_OPAQUE, "[redacted-opaque-value]"),
    )
    for pattern, replacement in replacements:
        text = pattern.sub(replacement, text)
    if len(text) > MAX_PUBLIC_EXCERPT_CHARS:
        text = text[: MAX_PUBLIC_EXCERPT_CHARS - 1].rstrip() + "…"
    return text or "Observed public event"


def build_calibration_pack(
    *,
    events_db: str | Path,
    shadow_db: str | Path,
    source_sha: str,
) -> dict[str, Any]:
    events = {
        str(event["event_id"]): event
        for event in MetaEventStore(events_db).events()
    }
    decisions = MetaShadowStore(shadow_db).calibration_rows()
    items: list[dict[str, Any]] = []
    bindings: list[dict[str, str]] = []
    for decision in decisions:
        event = events.get(str(decision["evidence_event_id"]))
        if event is None:
            raise MetaShadowHold("HOLD_CALIBRATION_EVENT_NOT_FOUND")
        excerpt = minimize_public_content(_public_text(event["normalized"]))
        item = {
            "decision_id": decision["decision_id"],
            "platform": decision["platform"],
            "object_type": event["object_type"],
            "public_content_excerpt": excerpt,
            "action": decision["action"],
            "risk_class": decision["risk_class"],
            "rationale": decision["rationale"],
            "state": decision["state"],
            "ratings": {field: decision["ratings"][field] for field in EVALUATION_FIELDS},
        }
        items.append(item)
        bindings.append(
            {
                "decision_id": str(decision["decision_id"]),
                "content_sha256": sha256(excerpt.encode("utf-8")).hexdigest(),
            }
        )
    calibration_set_id = "msc_" + sha256(canonical_json(bindings).encode("utf-8")).hexdigest()
    return {
        "schema": PACK_SCHEMA,
        "source_sha": source_sha,
        "calibration_set_id": calibration_set_id,
        "item_count": len(items),
        "content_policy": {
            "public_content_only": True,
            "direct_identifiers_redacted": True,
            "maximum_excerpt_characters": MAX_PUBLIC_EXCERPT_CHARS,
            "external_object_ids_included": False,
            "timestamps_included": False,
            "urls_included": False,
        },
        "evaluation_fields": list(EVALUATION_FIELDS),
        "allowed_rating_values": ["PASS", "FAIL", "NOT_APPLICABLE"],
        "safety": {
            "live_write": False,
            "kill_switch_engaged": True,
            "threads": "HOLD_EXTERNAL",
            "external_write_count": 0,
        },
        "items": items,
    }


def build_encrypted_manifest(
    *,
    plaintext_pack: str | Path,
    ciphertext: str | Path,
    recipient_certificate: str | Path,
) -> dict[str, Any]:
    pack = json.loads(Path(plaintext_pack).read_text(encoding="utf-8"))
    if pack.get("schema") != PACK_SCHEMA:
        raise MetaShadowHold("HOLD_CALIBRATION_PACK_SCHEMA")
    cipher_bytes = Path(ciphertext).read_bytes()
    certificate_bytes = Path(recipient_certificate).read_bytes()
    return {
        "schema": MANIFEST_SCHEMA,
        "calibration_set_id": pack["calibration_set_id"],
        "item_count": pack["item_count"],
        "encryption": "OPENSSL_CMS_AES_256_CBC_RSA_4096",
        "ciphertext_sha256": sha256(cipher_bytes).hexdigest(),
        "recipient_certificate_sha256": sha256(certificate_bytes).hexdigest(),
        "plaintext_persisted": False,
        "secrets_included": False,
        "external_write_count": 0,
    }


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    Path(path).write_text(canonical_json(value) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ppos-meta-calibration-pack")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--events-db", required=True)
    build.add_argument("--shadow-db", required=True)
    build.add_argument("--source-sha", required=True)
    build.add_argument("--output", required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--plaintext-pack", required=True)
    manifest.add_argument("--ciphertext", required=True)
    manifest.add_argument("--recipient-certificate", required=True)
    manifest.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        _write_json(
            args.output,
            build_calibration_pack(
                events_db=args.events_db,
                shadow_db=args.shadow_db,
                source_sha=args.source_sha,
            ),
        )
        return 0
    _write_json(
        args.output,
        build_encrypted_manifest(
            plaintext_pack=args.plaintext_pack,
            ciphertext=args.ciphertext,
            recipient_certificate=args.recipient_certificate,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
