"""The provider-portability fixture corpus gate (#2123, design v3 section 16 item i).

Provenance-agnostic: every assertion is driven by ``provenance.json`` rather
than by a hard-coded expectation per file, so adding a captured body is a data
change. Three jobs:

* **Shape.** Every fixture validates through production's own dispatch and
  looks like a Codex Responses body.
* **Recorded verdict.** The view and the portability verdict match what
  provenance records. A real captured body that cannot overflow is therefore
  green *and states that fact* -- neither a red CI nor a fake pass.
* **Sync.** Files on disk, ``provenance.json`` rows and ``README.md`` rows
  agree in both directions, and the privacy gate passes.

Assertion messages always carry the provenance, because "fixture X is not
portable" is meaningless without knowing which client version produced it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from app.core.types import JsonValue
from app.modules.model_sources.projection import (
    OVERFLOW_VIEW_FIELDS,
    STRIPPED_STREAM_OPTIONS_KEYS,
    STRIPPED_TELEMETRY_FIELDS,
    Declined,
    PortabilityView,
    overflow_portability_view,
    strip_source_telemetry,
)
from app.modules.proxy.api import _has_openai_responses_shape
from app.modules.proxy.replay_safety import PortabilityVerdict, responses_payload_is_provider_portable
from app.modules.proxy.request_policy import normalize_responses_request_payload
from scripts.traffic_analysis import fixture_privacy_scan
from scripts.traffic_analysis.codex_body_sanitize import (
    DROPPED_TOP_LEVEL_FIELDS,
    SANITISED_STREAM_OPTIONS_KEYS,
    SHAPE_PRESERVED_TOP_LEVEL_FIELDS,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex_bodies"
PROVENANCE_NAME = "provenance.json"
README_NAME = "README.md"

# Fields a fixture may carry beyond the portability view: the Codex telemetry a
# pre-strip fixture exists to feed to ``strip_source_telemetry``.
_PRE_STRIP_FIELDS = STRIPPED_TELEMETRY_FIELDS | {"stream_options", "service_tier"}

# The brief's byte-preserve list, intersected with the view allowlist: the
# sanitiser must never drop or fabricate any of these (drift guard).
_MUST_SURVIVE_SANITISATION = (
    frozenset(
        {
            "instructions",
            "tools",
            "input",
            "reasoning",
            "include",
            "text",
            "store",
            "stream",
            "parallel_tool_calls",
            "tool_choice",
        }
    )
    & OVERFLOW_VIEW_FIELDS
)

_README_ROW = re.compile(r"^\|\s*`(?P<name>[A-Za-z0-9_.-]+\.json)`\s*\|\s*(?P<origin>captured|synthetic)\s*\|")


def _provenance() -> dict[str, dict[str, Any]]:
    payload = json.loads((FIXTURES / PROVENANCE_NAME).read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return cast(dict[str, dict[str, Any]], payload["fixtures"])


PROVENANCE = _provenance()
FIXTURE_NAMES = sorted(PROVENANCE)


def _body_files() -> list[str]:
    return sorted(path.name for path in FIXTURES.glob("*.json") if path.name != PROVENANCE_NAME)


def _load(name: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _label(name: str) -> str:
    entry = PROVENANCE[name]
    return (
        f"{name} [{entry['origin']}, slug={entry['model_slug']}, "
        f"codex={entry['codex_version'] or 'n/a'}, transport={entry['transport']}]"
    )


def _stripped(name: str) -> dict[str, JsonValue]:
    """The body as the overflow decision sees it, through production's own dispatch.

    ``normalize_responses_request_payload`` with ``openai_compat`` from
    ``_has_openai_responses_shape`` is the *only* correct entry point: a real
    Responses-Lite body has no ``instructions`` key, so bare
    ``ResponsesRequest.model_validate`` fails with ``instructions Field
    required`` and the gate would red-line for the wrong reason the day a
    capture lands.
    """

    body = _load(name)
    payload = normalize_responses_request_payload(dict(body), openai_compat=_has_openai_responses_shape(body))
    return strip_source_telemetry(payload.model_dump_for_forwarding(), strip_service_tier=True)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_validates_through_the_production_dispatch(name: str) -> None:
    body = _load(name)

    payload = normalize_responses_request_payload(dict(body), openai_compat=_has_openai_responses_shape(body))

    assert payload.model == body["model"], _label(name)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_is_shaped_like_a_codex_responses_body(name: str) -> None:
    body = _load(name)
    label = _label(name)

    assert isinstance(body["model"], str) and body["model"], label
    input_items = body["input"]
    assert isinstance(input_items, list) and input_items, label
    for item in input_items:
        assert isinstance(item, dict), label
        for field in ("type", "role"):
            if field in item:
                assert isinstance(item[field], str), f"{label}: {field}"
    assert body["store"] is False, label
    assert body["stream"] is True, label
    assert body["include"] == ["reasoning.encrypted_content"], label
    assert isinstance(body["reasoning"], dict), label
    # Exactly one tool surface: the standard ``tools`` array or the Lite bundle.
    has_tools = "tools" in body
    has_bundle = any(isinstance(item, dict) and item.get("type") == "additional_tools" for item in input_items)
    assert has_tools != has_bundle, f"{label}: tools={has_tools} additional_tools={has_bundle}"
    # No field production has never seen; nothing fabricated beyond the corpus contract.
    assert set(body) <= OVERFLOW_VIEW_FIELDS | _PRE_STRIP_FIELDS, label


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_telemetry_presence_matches_the_declared_fixture_role(name: str) -> None:
    """A sanitised fixture carries no telemetry; a pre-strip fixture must carry it."""

    body = _load(name)
    entry = PROVENANCE[name]
    carries = bool(entry["carries_client_telemetry"])

    present = sorted(field for field in DROPPED_TOP_LEVEL_FIELDS if field in body)

    assert bool(present) == carries, f"{_label(name)}: declared {carries}, found {present}"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_matches_its_recorded_view_and_verdict(name: str) -> None:
    entry = PROVENANCE[name]
    label = _label(name)
    stripped = _stripped(name)

    view = overflow_portability_view(stripped)

    if entry["expected_view"] == "view":
        assert isinstance(view, PortabilityView), f"{label}: {view}"
        assert entry["expected_view_decline"] is None, label
        classified = view
    else:
        assert isinstance(view, Declined), f"{label}: expected a decline"
        decline = entry["expected_view_decline"]
        assert (view.reason, view.detail) == (decline["reason"], decline["detail"]), label
        # A view the builder declined is still classified, from the stripped
        # body, so the recorded verdict covers the Lite lane too.
        classified = PortabilityView(body=stripped)

    expected = entry["expected_portability_verdict"]
    verdict = responses_payload_is_provider_portable(
        classified,
        {},
        supported_tool_types=frozenset(expected["declared_tool_types"]),
        supports_vision=bool(expected["supports_vision"]),
    )

    assert verdict == PortabilityVerdict(bool(expected["portable"]), expected["reason"], expected["detail"]), (
        f"{label}: declared {sorted(expected['declared_tool_types'])} -> {verdict}"
    )


def test_a_captured_body_records_that_native_codex_traffic_cannot_overflow() -> None:
    """Design section 16 item (i) existed to discover this; the corpus must state it.

    Not a synthetic construction: the real gpt-5.5 body declines even with
    every tool type it declares marked supported, because ``tool_search``
    carries ``execution``, ``web_search`` carries ``external_web_access`` /
    ``search_content_types``, and every input item carries a prefixed id.
    """

    captured = {name for name, entry in PROVENANCE.items() if entry["origin"] == "captured"}
    assert captured, "the corpus must contain at least one captured body"

    for name in sorted(captured):
        entry = PROVENANCE[name]
        assert entry["codex_version"], _label(name)
        assert entry["catalog_sha256"], _label(name)
        assert entry["expected_portability_verdict"]["portable"] is False, _label(name)
        assert entry["expected_transcript_is_source_free"] is False, _label(name)


# --- cross-pins against the production constants -------------------------------------


def test_the_sanitiser_field_sets_are_pinned_against_production() -> None:
    """Drift guard: the sanitiser must remove at least what production removes."""

    assert DROPPED_TOP_LEVEL_FIELDS >= STRIPPED_TELEMETRY_FIELDS
    assert SANITISED_STREAM_OPTIONS_KEYS == STRIPPED_STREAM_OPTIONS_KEYS
    # Fields the overflow view reads must never be dropped or fabricated.
    assert SHAPE_PRESERVED_TOP_LEVEL_FIELDS >= _MUST_SURVIVE_SANITISATION
    assert not DROPPED_TOP_LEVEL_FIELDS & SHAPE_PRESERVED_TOP_LEVEL_FIELDS


# --- corpus sync ---------------------------------------------------------------------


def test_files_on_disk_and_provenance_rows_agree_in_both_directions() -> None:
    assert _body_files() == FIXTURE_NAMES


def test_readme_rows_and_provenance_rows_agree_in_both_directions() -> None:
    readme = (FIXTURES / README_NAME).read_text(encoding="utf-8")

    rows = {
        match.group("name"): match.group("origin") for line in readme.splitlines() if (match := _README_ROW.match(line))
    }

    assert sorted(rows) == FIXTURE_NAMES
    for name, origin in sorted(rows.items()):
        assert origin == PROVENANCE[name]["origin"], f"{_label(name)}: README says {origin}"


def test_the_fixture_privacy_gate_passes() -> None:
    """Pre-strip fixtures declare themselves; nothing else is exempt.

    ``provenance.json`` is exempted from the bare-telemetry-key kind for the
    same reason the README is prose: it *names* the fields the sanitiser
    removes. Every value-shaped kind (a live UUID, a workspace path, an email
    address, this host's identity, a credential shape) still applies to it.
    """

    exempt = {name for name, entry in PROVENANCE.items() if entry["carries_client_telemetry"]}

    report = fixture_privacy_scan.scan_fixture_tree(
        FIXTURES,
        allow_telemetry_keys=frozenset(exempt | {PROVENANCE_NAME}),
    )

    assert report["passed"] is True, report["findings"]
    assert report["bodies_scanned"] == len(FIXTURE_NAMES) + 1  # + provenance.json
