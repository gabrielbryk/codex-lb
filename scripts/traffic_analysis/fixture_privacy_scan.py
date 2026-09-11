"""Privacy gate for the committed Codex body fixtures.

Two passes over the same tree:

* **Credentials** -- ``privacy_scan.scan_tree`` unchanged, over every file. It
  already knows the shapes that must never be committed anywhere (token, API
  key, JWT, OAuth token fields).
* **Identifiers** -- this module, over the fixture bodies only (``*.json``).
  The credential scanner has no vocabulary for a workspace path, a live UUID or
  a telemetry key, and it *passes* the fixture tree today despite both fixtures
  carrying UUIDs and absolute paths.

The identifier pass deliberately skips ``*.md``: the corpus README has to be
able to name the keys the sanitiser removes, and prose is reviewed by a human,
not by shape. Credential shapes are still rejected in every file including the
README.

Every pattern is a *shape*, matched against the telemetry form rather than the
bare word, because the real Codex tool schema declares a legitimate numeric
``session_id`` parameter (the unified-exec session) -- a bare-word match would
red-line a genuine capture for the wrong reason. Findings never echo the
offending value.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json
    from scripts.traffic_analysis.codex_body_sanitize import is_placeholder_uuid, live_identity_strings
    from scripts.traffic_analysis.privacy_scan import scan_tree
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json
    from scripts.traffic_analysis.codex_body_sanitize import is_placeholder_uuid, live_identity_strings
    from scripts.traffic_analysis.privacy_scan import scan_tree

_CHUNK_BYTES = 1024 * 1024
_OVERLAP_BYTES = 512

IDENTIFIER_SCAN_SUFFIXES: frozenset[str] = frozenset({".json"})

_UUID = re.compile(rb"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

# Telemetry keys that carry a *string* identifier. ``"session_id": {`` (a tool
# parameter schema) and ``session_id?: number`` (prose in a tool description)
# are both legitimate Codex tool vocabulary and must not match, which is why
# the pattern requires a quoted value -- and why a placeholder value is fine.
_STRING_VALUED_IDENTIFIER_KEYS: tuple[str, ...] = (
    "installation_id",
    "root_turn_id",
    "session_id",
    "thread_id",
    "turn_id",
    "window_id",
)

# Bare telemetry key names. Unlike every other kind this one cannot be
# conditioned on the value, so a fixture that exists to exercise the production
# telemetry stripper declares itself in ``provenance.json`` and is exempted by
# name (``allow_telemetry_keys``) rather than being made unrepresentative.
TELEMETRY_KEY_KIND = "telemetry_field"

_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    # ``/root`` is not flagged: it identifies no operator, and Codex's own
    # ``spawn_agent`` description documents agent task namespaces as
    # ``/root/<task>`` -- flagging it would force mangling genuine content.
    ("home_path", re.compile(rb"(?:/home/|/Users/|/tmp/|/mnt/)")),
    (
        "identifier_key",
        re.compile(
            rb'"(?:'
            + rb"|".join(key.encode() for key in _STRING_VALUED_IDENTIFIER_KEYS)
            + rb')"\s*:\s*"(?!00000000-0000-4000-8000-\d{12}")',
        ),
    ),
    (
        TELEMETRY_KEY_KIND,
        re.compile(rb"(?:client_metadata|access_programs|chatgpt-account-id|x-codex-[a-z-]+)"),
    ),
    ("email", re.compile(rb"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)


def _identity_patterns() -> tuple[tuple[str, re.Pattern[bytes]], ...]:
    return tuple(
        ("live_identity", re.compile(rb"\b" + re.escape(identity.encode()) + rb"\b"))
        for identity in live_identity_strings()
    )


def _chunks(path: Path) -> Iterator[bytes]:
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            sample = tail + chunk
            yield sample
            tail = sample[-_OVERLAP_BYTES:]


def identifier_findings(path: Path) -> list[str]:
    """Identifier-shaped kinds found in ``path``; never the offending value."""

    kinds: set[str] = set()
    patterns = _PATTERNS + _identity_patterns()
    for sample in _chunks(path):
        for label, pattern in patterns:
            if pattern.search(sample):
                kinds.add(label)
        for match in _UUID.finditer(sample):
            if not is_placeholder_uuid(match.group().decode()):
                kinds.add("uuid")
                break
    return sorted(kinds)


def _identifier_pass(root: Path, allow_telemetry_keys: frozenset[str]) -> tuple[list[dict[str, Any]], int]:
    findings: list[dict[str, Any]] = []
    scanned = 0
    for current, _directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(file_names):
            candidate = current_path / name
            if candidate.is_symlink() or candidate.suffix.casefold() not in IDENTIFIER_SCAN_SUFFIXES:
                continue
            relative = str(candidate.relative_to(root))
            try:
                kinds = identifier_findings(candidate)
            except OSError as exc:
                findings.append({"path": relative, "kinds": [f"read_error:{type(exc).__name__}"]})
                continue
            scanned += 1
            if relative in allow_telemetry_keys:
                kinds = [kind for kind in kinds if kind != TELEMETRY_KEY_KIND]
            if kinds:
                findings.append({"path": relative, "kinds": kinds})
    return findings, scanned


def scan_fixture_tree(
    root: str | Path,
    *,
    allow_telemetry_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Credential scan over every file plus the identifier scan over the bodies.

    ``allow_telemetry_keys`` names the root-relative bodies that may carry bare
    Codex telemetry key names -- the pre-strip fixtures whose whole purpose is
    to feed ``strip_source_telemetry``. Every other kind (a live UUID, a
    workspace path, an email, this host's identity, a credential shape) is
    rejected in those files too.

    Report shape mirrors ``privacy_scan.scan_tree`` and adds
    ``bodies_scanned``; findings from both passes are merged per path.
    """

    root_path = Path(root).resolve()
    credential = scan_tree(root_path)
    identifier, bodies_scanned = _identifier_pass(root_path, allow_telemetry_keys)
    merged: dict[str, set[str]] = {}
    for finding in [*credential["findings"], *identifier]:
        merged.setdefault(str(finding["path"]), set()).update(finding["kinds"])
    findings = [{"path": path, "kinds": sorted(kinds)} for path, kinds in sorted(merged.items())]
    return {
        "schema_version": 1,
        "root": str(root_path),
        "passed": not findings,
        "files_scanned": credential["files_scanned"],
        "bodies_scanned": bodies_scanned,
        "bytes_scanned": credential["bytes_scanned"],
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--allow-telemetry-keys",
        action="append",
        default=[],
        metavar="NAME",
        help="Root-relative body that may keep bare Codex telemetry key names (pre-strip fixtures)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = scan_fixture_tree(args.root, allow_telemetry_keys=frozenset(args.allow_telemetry_keys))
    if args.output:
        atomic_write_json(args.output, result)
    print(
        f"Fixture privacy scan: {'PASS' if result['passed'] else 'FAIL'}; "
        f"{result['files_scanned']} file(s), {result['bodies_scanned']} body/bodies"
    )
    for finding in result["findings"]:
        print(f"  {finding['path']}: {', '.join(finding['kinds'])}")
    return 2 if args.strict and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
