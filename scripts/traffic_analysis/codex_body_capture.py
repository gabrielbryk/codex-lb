"""Capture a real Codex Responses request body with no upstream contact.

One command. No ChatGPT credentials, no quota, no network egress:

    python scripts/traffic_analysis/codex_body_capture.py \
      --model gpt-5.5 --model gpt-5.6-sol --transport http \
      --catalog /path/to/models_cache.json \
      --out /mnt/scratch/tmp/codex-body-capture-$(date -u +%Y%m%d)

How it stays isolated:

* **Kernel-enforced.** The script re-executes itself inside an unprivileged
  network namespace (``unshare --map-root-user --net`` plus ``ip link set lo
  up``), so loopback is the only reachable network. "No upstream Codex call" is
  a property of the sandbox, not of a code review.
* **No credentials.** A throwaway ``CODEX_HOME`` and a provider with
  ``requires_openai_auth = false`` and a disposable ``env_key`` token. The
  script refuses to start if the chosen home holds an ``auth.json`` or if the
  environment carries any production/proxy variable.
* **Captured in the origin, not in a proxy.** The loopback origin receives
  plaintext HTTP and persists the decoded request bytes itself, reusing
  ``origin_fixture.decode_request_body``. No TLS, no mitmproxy addon, no
  ``uv``.

Why the catalog must be pinned: the Codex model manager invalidates its cache
on a ``client_version`` mismatch and caches for 300 s, so a capture run always
refetches ``/models`` from whatever base URL the provider names. The origin
therefore serves an operator-supplied catalog file and records its digest as
provenance. The catalog does not fully determine the body -- 0.154.0 layers
bundled ``model_info`` overrides on top of it, which is why the CLI version is
the primary provenance key.

Nothing in this module runs in CI. The guards and the naming are pure
functions and are unit-tested; the orchestration needs ``codex``, ``unshare``
and a uvicorn thread.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_attestation
    from scripts.traffic_analysis.origin_fixture import (
        decode_request_body,
        loopback_host,
        response_events,
        sse_frames,
    )
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_attestation
    from scripts.traffic_analysis.origin_fixture import (
        decode_request_body,
        loopback_host,
        response_events,
        sse_frames,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT = 19090
DEFAULT_PROMPT = "Return exactly CAPTURE_OK.\n"
CAPTURE_TOKEN_VARIABLE = "CODEX_CAPTURE_TOKEN"
CAPTURE_TOKEN_VALUE = "non-secret-capture-token"
PROVIDER_NAME = "capture-origin"
REEXEC_MARKER = "CODEX_BODY_CAPTURE_NETNS"
TRANSPORTS = ("http", "websocket")

# Variables whose presence means a production or proxy configuration has bled
# into the shell. Mirrors ``traffic-parity-auth/env.sh``: a capture that picks
# up ``OPENAI_BASE_URL`` is no longer loopback-only, and one that picks up a
# ``CODEX_LB_*`` value is reading the operator's deployment.
FORBIDDEN_ENVIRONMENT_VARIABLES: frozenset[str] = frozenset(
    {
        "CHATGPT_BASE_URL",
        "CODEX_ACCESS_TOKEN",
        "CODEX_API_BASE_URL",
        "CODEX_SESSION_ID",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)
FORBIDDEN_ENVIRONMENT_PREFIXES: tuple[str, ...] = ("CODEX_LB_",)

# Storage policy: raw captures are long-lived multi-hundred-KB artifacts, and a
# ``CODEX_HOME`` under ``/tmp`` also makes Codex refuse to create its helper
# binaries ("Refusing to create helper binaries under temporary dir").
FORBIDDEN_OUTPUT_ROOTS: tuple[str, ...] = ("/tmp", "/var/tmp", "/dev/shm")
SUGGESTED_OUTPUT_ROOTS: tuple[str, ...] = ("/mnt/scratch/tmp", "~/tmp")


class CaptureRefusal(RuntimeError):
    """A precondition failed. Raised before any process or server starts."""


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    model_slug: str
    transport: str


def assert_output_outside_repo(
    destination: Path,
    *,
    repo_root: Path = REPO_ROOT,
    forbidden_roots: tuple[str, ...] = FORBIDDEN_OUTPUT_ROOTS,
) -> Path:
    """Resolve ``destination`` or refuse it.

    Refuses a path inside the repository (a capture is never committed raw), a
    symlink anywhere on the way (the resolved target would escape the check),
    and any location under a temporary filesystem.
    """

    if destination.is_symlink():
        raise CaptureRefusal(f"output directory must not be a symlink: {destination}")
    resolved = destination.expanduser().resolve()
    if resolved == repo_root.resolve() or repo_root.resolve() in resolved.parents:
        raise CaptureRefusal(
            f"output directory must live outside the repository: {resolved} is inside {repo_root}. "
            f"Use one of {', '.join(SUGGESTED_OUTPUT_ROOTS)}."
        )
    for root in forbidden_roots:
        root_path = Path(root)
        if resolved == root_path or root_path in resolved.parents:
            raise CaptureRefusal(
                f"output directory must not live under {root} (storage policy): {resolved}. "
                f"Use one of {', '.join(SUGGESTED_OUTPUT_ROOTS)}."
            )
    return resolved


def assert_clean_environment(environment: Mapping[str, str]) -> None:
    """Refuse a shell carrying production, proxy or upstream configuration."""

    offenders = sorted(
        name
        for name in environment
        if name in FORBIDDEN_ENVIRONMENT_VARIABLES or name.startswith(FORBIDDEN_ENVIRONMENT_PREFIXES)
    )
    if offenders:
        raise CaptureRefusal(
            "refusing to capture with production/proxy configuration in the environment: "
            f"{', '.join(offenders)}. Run from a clean shell."
        )


def assert_home_uncredentialed(home: Path) -> None:
    """Refuse a ``CODEX_HOME`` that holds ChatGPT credentials."""

    if (home / "auth.json").exists():
        raise CaptureRefusal(
            f"refusing to capture with a credentialed CODEX_HOME: {home / 'auth.json'} exists. "
            "The capture lane uses a throwaway home and a disposable provider token."
        )


def assert_loopback_base_url(base_url: str) -> None:
    """Refuse an origin the capture cannot prove is local."""

    parts = urlsplit(base_url)
    if parts.scheme != "http" or not parts.hostname or not loopback_host(parts.hostname):
        raise CaptureRefusal(f"capture origin base URL must be loopback HTTP: {base_url}")


def assert_catalog_path(catalog: Path) -> Path:
    """Refuse a catalog that is really configuration or a secret file."""

    if catalog.is_symlink():
        raise CaptureRefusal(f"catalog must not be a symlink: {catalog}")
    resolved = catalog.expanduser().resolve()
    if not resolved.is_file():
        raise CaptureRefusal(f"catalog file not found: {resolved}")
    if resolved.name.startswith(".env") or resolved.suffix == ".env":
        raise CaptureRefusal(f"refusing to serve an environment file as a model catalog: {resolved}")
    if (REPO_ROOT / "config").resolve() in resolved.parents:
        raise CaptureRefusal(f"refusing to serve repository configuration as a model catalog: {resolved}")
    return resolved


def capture_artifact_name(kind: str, target: CaptureTarget, started_at: datetime) -> str:
    """Filename keyed by slug, transport and run start, so runs never collide.

    The prototype numbered bodies with a per-process counter, which collided
    across runs. A dot survives (``gpt-5.5``) but a parent reference does not:
    the slug reaches this function straight from ``--model``.
    """

    slug = "".join(character if character.isalnum() or character in "-." else "-" for character in target.model_slug)
    while ".." in slug:
        slug = slug.replace("..", "-")
    return f"{kind}-{slug}-{target.transport}-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def capture_config_toml(*, model_slug: str, base_url: str, transport: str) -> str:
    """The Codex configuration for one capture run.

    ``request_max_retries``/``stream_max_retries`` are zero so a mistake fails
    the run instead of quietly producing several bodies.
    """

    return "\n".join(
        (
            f'model = "{model_slug}"',
            f'model_provider = "{PROVIDER_NAME}"',
            "",
            f"[model_providers.{PROVIDER_NAME}]",
            'name = "openai"',
            f'base_url = "{base_url}"',
            'wire_api = "responses"',
            f'env_key = "{CAPTURE_TOKEN_VARIABLE}"',
            f"supports_websockets = {'true' if transport == 'websocket' else 'false'}",
            "requires_openai_auth = false",
            "request_max_retries = 0",
            "stream_max_retries = 0",
            "",
        )
    )


def capture_environment(environment: Mapping[str, str], *, home: Path) -> dict[str, str]:
    """The child environment: the caller's, minus upstream pointers, plus the throwaway home."""

    child = {
        name: value
        for name, value in environment.items()
        if name not in FORBIDDEN_ENVIRONMENT_VARIABLES and not name.startswith(FORBIDDEN_ENVIRONMENT_PREFIXES)
    }
    child["CODEX_HOME"] = str(home)
    child[CAPTURE_TOKEN_VARIABLE] = CAPTURE_TOKEN_VALUE
    return child


def body_summary(body: Mapping[str, Any]) -> dict[str, Any]:
    """The one-line facts an operator checks before sanitising."""

    tools = body.get("tools")
    input_items = body.get("input")
    return {
        "top_level_keys": sorted(body),
        "instructions_present": "instructions" in body,
        "tool_types": (
            sorted({str(tool.get("type")) for tool in tools if isinstance(tool, dict)})
            if isinstance(tools, list)
            else None
        ),
        "item_sequence": (
            [f"{item.get('type', '?')}/{item.get('role', '-')}" for item in input_items if isinstance(item, dict)]
            if isinstance(input_items, list)
            else None
        ),
    }


def _build_origin(
    catalog: Path,
    destination: Path,
    started_at: datetime,
) -> tuple[FastAPI, list[dict[str, Any]]]:
    """The capture origin: serves the pinned catalog, persists decoded bodies.

    ``Request`` must be resolvable from module globals: with deferred
    annotations, a function-local import leaves FastAPI unable to see the
    parameter as the request object and it answers ``422`` on a query
    parameter that was never sent.
    """

    catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
    captured: list[dict[str, Any]] = []
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.target = None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "codex-body-capture-origin"}

    @app.get("/models")
    @app.get("/v1/models")
    @app.get("/codex/models")
    @app.get("/backend-api/codex/models")
    async def models() -> JSONResponse:
        return JSONResponse(catalog_payload)

    @app.post("/v1/responses")
    @app.post("/codex/responses")
    @app.post("/backend-api/codex/responses")
    async def responses(request: Request) -> Response:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
        decoded = decode_request_body(bytes(raw), request.headers.get("content-encoding"))
        payload = json.loads(decoded)
        target: CaptureTarget = request.app.state.target
        body_path = destination / capture_artifact_name("body", target, started_at)
        headers_path = destination / capture_artifact_name("headers", target, started_at)
        if body_path.exists():
            # A retry or a second turn: keep the first body, never overwrite it.
            captured.append({"model_slug": target.model_slug, "transport": target.transport, "extra_turn": True})
        else:
            body_path.write_bytes(decoded)
            atomic_write_json(headers_path, dict(request.headers))
            captured.append(
                {
                    "model_slug": target.model_slug,
                    "transport": target.transport,
                    "extra_turn": False,
                    "body": file_attestation("body", body_path),
                    "headers": file_attestation("headers", headers_path),
                    "summary": body_summary(payload),
                }
            )
        events = response_events()
        if payload.get("stream") is True:
            return StreamingResponse(sse_frames(events), media_type="text/event-stream")
        return JSONResponse(events[-1]["response"])

    return app, captured


def _reexec_in_network_namespace(argv: Sequence[str]) -> int:
    """Re-run this script with loopback as the only reachable network."""

    if shutil.which("unshare") is None or shutil.which("ip") is None:
        raise CaptureRefusal(
            "unshare and ip are required for the isolated capture lane; "
            "pass --no-network-namespace --i-accept-network-egress to opt out"
        )
    inner = 'ip link set lo up && exec "$@"'
    command = [
        "unshare",
        "--map-root-user",
        "--net",
        "--",
        "sh",
        "-c",
        inner,
        "sh",
        sys.executable,
        str(Path(__file__).resolve()),
        *argv,
    ]
    environment = dict(os.environ)
    environment[REEXEC_MARKER] = "1"
    return subprocess.run(command, env=environment, check=False).returncode


def _serve(app: Any, port: int) -> tuple[Any, threading.Thread]:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", server_header=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="codex-body-capture-origin", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if server.started:
            return server, thread
        time.sleep(0.05)
    server.should_exit = True
    raise CaptureRefusal(f"capture origin did not start on 127.0.0.1:{port}")


def _run_codex(
    *,
    codex_binary: str,
    model_slug: str,
    workdir: Path,
    environment: Mapping[str, str],
    prompt: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    command = [
        codex_binary,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-C",
        str(workdir),
        "-s",
        "read-only",
        "-m",
        model_slug,
        prompt,
    ]
    # ``codex exec`` blocks on "Reading additional input from stdin..." whenever
    # stdin is a pipe, so it must be closed rather than inherited.
    return subprocess.run(
        command,
        cwd=workdir,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture real Codex request bodies against a loopback origin.")
    parser.add_argument("--model", action="append", required=True, metavar="SLUG", help="Repeatable model slug")
    parser.add_argument("--transport", choices=TRANSPORTS, default="http")
    parser.add_argument("--out", type=Path, required=True, help="Capture directory outside the repository")
    parser.add_argument("--catalog", type=Path, required=True, help="Pinned /models catalog served to Codex")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--codex-bin", default=shutil.which("codex"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--keep-home", action="store_true", help="Keep the throwaway CODEX_HOME for inspection")
    parser.add_argument("--no-network-namespace", action="store_true")
    parser.add_argument(
        "--i-accept-network-egress",
        action="store_true",
        help="Required companion of --no-network-namespace: egress is then only policy, not kernel",
    )
    return parser


def _preflight(args: argparse.Namespace) -> tuple[Path, Path, str]:
    if args.no_network_namespace and not args.i_accept_network_egress:
        raise CaptureRefusal("--no-network-namespace requires --i-accept-network-egress")
    if not args.codex_bin:
        raise CaptureRefusal("codex binary not found; pass --codex-bin")
    assert_clean_environment(os.environ)
    destination = assert_output_outside_repo(args.out)
    catalog = assert_catalog_path(args.catalog)
    base_url = f"http://127.0.0.1:{args.port}/v1"
    assert_loopback_base_url(base_url)
    return destination, catalog, base_url


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
    try:
        destination, catalog, base_url = _preflight(args)
    except CaptureRefusal as exc:
        print(f"Refusing to capture: {exc}", file=sys.stderr)
        return 2

    if not args.no_network_namespace and os.environ.get(REEXEC_MARKER) != "1":
        try:
            return _reexec_in_network_namespace(raw_argv)
        except CaptureRefusal as exc:
            print(f"Refusing to capture: {exc}", file=sys.stderr)
            return 2

    started_at = datetime.now(UTC)
    destination.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="codex-capture-home-", dir=destination))
    workdir = Path(tempfile.mkdtemp(prefix="codex-capture-work-", dir=destination))
    try:
        assert_home_uncredentialed(home)
        atomic_write_text(
            workdir / "AGENTS.md",
            "Run the unit tests before declaring a change done.\n",
        )
        app, captured = _build_origin(catalog, destination, started_at)
        server, thread = _serve(app, args.port)
        print(
            "network namespace: "
            + ("loopback only (unshare --net)" if not args.no_network_namespace else "DISABLED by operator")
        )
        print(f"capture origin: {base_url} (health ok)")
        catalog_attestation = file_attestation("catalog", catalog)
        print(f"catalog: {catalog.name} sha256={catalog_attestation['sha256']}")
        codex_version = subprocess.run(
            [args.codex_bin, "--version"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
        print(f"codex: {codex_version}")

        runs: list[dict[str, Any]] = []
        for model_slug in args.model:
            target = CaptureTarget(model_slug=model_slug, transport=args.transport)
            app.state.target = target
            atomic_write_text(
                home / "config.toml",
                capture_config_toml(model_slug=model_slug, base_url=base_url, transport=args.transport),
            )
            environment = capture_environment(os.environ, home=home)
            try:
                completed = _run_codex(
                    codex_binary=args.codex_bin,
                    model_slug=model_slug,
                    workdir=workdir,
                    environment=environment,
                    prompt=args.prompt,
                    timeout_seconds=args.timeout_seconds,
                )
                exit_code: int | None = completed.returncode
                tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-3:]
            except subprocess.TimeoutExpired:
                exit_code = None
                tail = ["timed out"]
            record = next(
                (item for item in reversed(captured) if item["model_slug"] == model_slug and not item["extra_turn"]),
                None,
            )
            runs.append(
                {
                    "model_slug": model_slug,
                    "transport": args.transport,
                    "exit_code": exit_code,
                    **(record or {}),
                }
            )
            if record is None:
                print(f"captured {model_slug:16s} {args.transport} NOTHING exit={exit_code} {' | '.join(tail)}")
                continue
            attestation = record["body"]
            summary = record["summary"]
            print(
                f"captured {model_slug:16s} {args.transport} {attestation['bytes']} B "
                f"sha256={str(attestation['sha256'])[:16]}... exit={exit_code}"
            )
            print(f"                 keys={','.join(summary['top_level_keys'])}")
            print(
                f"                 tools={summary['tool_types']} items={summary['item_sequence']} "
                f"instructions={'present' if summary['instructions_present'] else 'ABSENT'}"
            )
        server.should_exit = True
        thread.join(timeout=10.0)
        manifest = {
            "schema_version": 1,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "codex_version": codex_version,
            "transport": args.transport,
            "network_namespace": not args.no_network_namespace,
            "catalog": catalog_attestation,
            "runs": runs,
        }
        atomic_write_json(destination / "manifest.json", manifest)
        print(f"wrote: {destination}/{{body,headers}}-*.json, manifest.json")
        print(
            "next: python scripts/traffic_analysis/codex_body_sanitize.py "
            f"--in {destination}/body-<slug>-{args.transport}-<stamp>.json --out <fixture>.json"
        )
        return 0 if all(run.get("body") for run in runs) else 1
    except CaptureRefusal as exc:
        print(f"Refusing to capture: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if not args.keep_home:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
