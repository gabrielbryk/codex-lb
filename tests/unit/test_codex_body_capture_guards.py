"""Refusals and naming of the isolated Codex body capture lane (#2123).

Pure functions only. CI never runs ``codex``, ``unshare`` or a uvicorn thread;
what it must guarantee is that every refusal fires *before* a process starts,
because the whole safety argument of the lane ("no credentials, no upstream, no
quota") rests on those preconditions rather than on operator discipline.

Each guard gets a positive case and a refusing case.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.traffic_analysis.codex_body_capture import (
    CAPTURE_TOKEN_VARIABLE,
    FORBIDDEN_ENVIRONMENT_PREFIXES,
    FORBIDDEN_ENVIRONMENT_VARIABLES,
    PROVIDER_NAME,
    REPO_ROOT,
    CaptureRefusal,
    CaptureTarget,
    assert_catalog_path,
    assert_clean_environment,
    assert_home_uncredentialed,
    assert_loopback_base_url,
    assert_output_outside_repo,
    body_summary,
    capture_artifact_name,
    capture_config_toml,
    capture_environment,
)

pytestmark = pytest.mark.unit

_STARTED_AT = datetime(2026, 9, 11, 17, 29, 52, tzinfo=UTC)


# --- assert_output_outside_repo -------------------------------------------------------


def test_output_outside_the_repository_is_accepted(tmp_path: Path) -> None:
    destination = tmp_path / "captures"

    # ``tmp_path`` is itself under ``/tmp``, so the temporary-filesystem roots
    # are injected rather than the real ones; the refusal is covered below.
    resolved = assert_output_outside_repo(destination, repo_root=tmp_path / "repo", forbidden_roots=("/nonexistent",))

    assert resolved == destination.resolve()


def test_output_inside_the_repository_is_refused() -> None:
    """A raw capture is never committed, so it may not even be written in the tree."""

    with pytest.raises(CaptureRefusal, match="outside the repository"):
        assert_output_outside_repo(REPO_ROOT / "scripts" / "traffic_analysis" / "output")


def test_output_under_a_temporary_filesystem_is_refused() -> None:
    """Storage policy, and Codex refuses to create helper binaries under a temporary dir."""

    with pytest.raises(CaptureRefusal, match="storage policy"):
        assert_output_outside_repo(Path("/tmp/codex-body-capture"))


def test_a_symlinked_output_directory_is_refused(tmp_path: Path) -> None:
    """Resolving first would let the symlink target escape the repository check."""

    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(CaptureRefusal, match="symlink"):
        assert_output_outside_repo(link, repo_root=tmp_path / "repo", forbidden_roots=("/nonexistent",))


# --- assert_clean_environment ---------------------------------------------------------


def test_a_clean_environment_is_accepted() -> None:
    assert_clean_environment({"PATH": "/usr/bin", "HOME": "/home/someone"})


@pytest.mark.parametrize("variable", sorted(FORBIDDEN_ENVIRONMENT_VARIABLES))
def test_an_upstream_pointer_in_the_environment_is_refused(variable: str) -> None:
    with pytest.raises(CaptureRefusal, match=variable):
        assert_clean_environment({variable: "value"})


def test_a_proxy_configuration_prefix_in_the_environment_is_refused() -> None:
    variable = f"{FORBIDDEN_ENVIRONMENT_PREFIXES[0]}UPSTREAM_BASE_URL"

    with pytest.raises(CaptureRefusal, match=variable):
        assert_clean_environment({variable: "https://example.invalid"})


# --- assert_home_uncredentialed -------------------------------------------------------


def test_a_throwaway_home_is_accepted(tmp_path: Path) -> None:
    assert_home_uncredentialed(tmp_path)


def test_a_credentialed_home_is_refused(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CaptureRefusal, match="credentialed CODEX_HOME"):
        assert_home_uncredentialed(tmp_path)


# --- assert_loopback_base_url ---------------------------------------------------------


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:19090/v1", "http://localhost:19090/v1", "http://[::1]:1/v1"])
def test_a_loopback_origin_is_accepted(base_url: str) -> None:
    assert_loopback_base_url(base_url)


@pytest.mark.parametrize(
    "base_url",
    ["http://10.0.0.113:19090/v1", "https://chatgpt.com/backend-api", "http://example.invalid/v1", "not-a-url"],
)
def test_a_non_loopback_origin_is_refused(base_url: str) -> None:
    with pytest.raises(CaptureRefusal, match="loopback HTTP"):
        assert_loopback_base_url(base_url)


# --- assert_catalog_path --------------------------------------------------------------


def test_a_catalog_file_is_accepted(tmp_path: Path) -> None:
    catalog = tmp_path / "models_cache.json"
    catalog.write_text('{"models": []}', encoding="utf-8")

    assert assert_catalog_path(catalog) == catalog.resolve()


def test_a_missing_catalog_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CaptureRefusal, match="not found"):
        assert_catalog_path(tmp_path / "absent.json")


def test_an_environment_file_as_catalog_is_refused(tmp_path: Path) -> None:
    secrets = tmp_path / ".env.production"
    secrets.write_text("TOKEN=value\n", encoding="utf-8")

    with pytest.raises(CaptureRefusal, match="environment file"):
        assert_catalog_path(secrets)


def test_repository_configuration_as_catalog_is_refused() -> None:
    with pytest.raises(CaptureRefusal, match="repository configuration|not found"):
        assert_catalog_path(REPO_ROOT / "config" / "settings.json")


# --- naming, configuration and the run environment ------------------------------------


def test_artifact_names_are_keyed_by_slug_transport_and_run_start() -> None:
    """The prototype's per-process counter collided across runs."""

    standard = capture_artifact_name("body", CaptureTarget("gpt-5.5", "http"), _STARTED_AT)
    lite = capture_artifact_name("body", CaptureTarget("gpt-5.6-sol", "websocket"), _STARTED_AT)

    assert standard == "body-gpt-5.5-http-20260911T172952Z.json"
    assert lite == "body-gpt-5.6-sol-websocket-20260911T172952Z.json"
    assert standard != lite


def test_a_path_separator_in_a_slug_cannot_escape_the_capture_directory(tmp_path: Path) -> None:
    name = capture_artifact_name("body", CaptureTarget("../../etc/passwd", "http"), _STARTED_AT)

    assert "/" not in name and ".." not in name
    assert (tmp_path / name).resolve().parent == tmp_path.resolve()


@pytest.mark.parametrize(
    ("transport", "expected"),
    [("http", "supports_websockets = false"), ("websocket", "supports_websockets = true")],
)
def test_the_generated_configuration_names_only_the_loopback_provider(transport: str, expected: str) -> None:
    config = capture_config_toml(model_slug="gpt-5.5", base_url="http://127.0.0.1:19090/v1", transport=transport)

    assert f'model_provider = "{PROVIDER_NAME}"' in config
    assert "requires_openai_auth = false" in config
    assert f'env_key = "{CAPTURE_TOKEN_VARIABLE}"' in config
    assert expected in config
    # Zero retries: a mistake fails the run instead of quietly capturing twice.
    assert "request_max_retries = 0" in config and "stream_max_retries = 0" in config


def test_the_child_environment_drops_upstream_pointers_and_names_the_throwaway_home(tmp_path: Path) -> None:
    parent = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-live-should-not-propagate",
        "CODEX_LB_UPSTREAM_BASE_URL": "https://example.invalid",
        "CODEX_HOME": "/home/someone/.codex",
    }

    child = capture_environment(parent, home=tmp_path)

    assert_clean_environment(child)
    assert child["CODEX_HOME"] == str(tmp_path)
    assert child[CAPTURE_TOKEN_VARIABLE]
    assert child["PATH"] == "/usr/bin"


def test_the_body_summary_reports_the_facts_an_operator_checks() -> None:
    summary = body_summary(
        {
            "model": "gpt-5.5",
            "instructions": "base",
            "tools": [{"type": "function"}, {"type": "tool_search"}, {"type": "web_search"}],
            "input": [{"type": "message", "role": "developer"}, {"type": "message", "role": "user"}],
        }
    )

    assert summary["tool_types"] == ["function", "tool_search", "web_search"]
    assert summary["item_sequence"] == ["message/developer", "message/user"]
    assert summary["instructions_present"] is True


def test_the_body_summary_reports_an_absent_instructions_key() -> None:
    """The Lite lane's distinguishing fact, and the one the synthetic pair hid."""

    summary = body_summary({"model": "gpt-5.6-sol", "input": [{"type": "additional_tools", "role": "developer"}]})

    assert summary["instructions_present"] is False
    assert summary["tool_types"] is None
    assert summary["item_sequence"] == ["additional_tools/developer"]
