"""Opt-in live acceptance for an existing WebUI script generation.

The default mode performs no database or HTTP work.  ``--live`` reads the
existing generation owner only to mint an in-memory, short-lived JWT, then
uses the localhost HTTP API exactly as the UI does.  It never creates a
generation, saves a case, repairs a draft, writes token files, or prints a
draft, runtime values, or server response bodies.

Example (run from ``backend``)::

    python scripts/verify_webui_generation_live.py --live \
      --generation-id 894a3fd5-0802-43f1-a28d-87aa57bb875b --debug-runs 2
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID


BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
ACTIVE_GENERATION_STATUSES = frozenset({
    "created", "normalizing", "preflighting", "exploring", "generating",
    "validating", "repairing",
})
ELIGIBLE_GENERATION_STATUSES = frozenset({"ready", "ready_with_warnings"})
BUSY_STATUSES = frozenset({"pending", "running"})
MAX_EVIDENCE_COUNT = 1_000_000


class LiveAcceptanceError(RuntimeError):
    """A compact, non-sensitive acceptance failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _request, _fp, _code, _message, _headers, _newurl):
        return None


_LOCAL_HTTP_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def loopback_open(request: Request, timeout: int):
    """Perform one direct request; redirects never get the in-memory JWT."""
    return _LOCAL_HTTP_OPENER.open(request, timeout=timeout)


def parse_generation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (AttributeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("generation-id must be a UUID") from exc


def bounded_integer(minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed
    return parse


def validate_loopback_base_url(value: str) -> str:
    """Accept a root-only HTTP(S) URL whose host is unambiguously loopback."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise argparse.ArgumentTypeError("base-url must be a loopback HTTP(S) origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("base-url has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("base-url has an invalid port")
    return f"{parsed.scheme}://{parsed.netloc}"


def ensure_resolved_loopback(base_url: str) -> None:
    """Resolve the accepted host only in live mode, before a JWT is minted."""
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        raise LiveAcceptanceError("BASE_URL_INVALID")
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        resolved = {item[4][0] for item in addresses}
    except socket.gaierror as exc:
        raise LiveAcceptanceError("LOOPBACK_RESOLUTION_FAILED") from exc
    if not resolved:
        raise LiveAcceptanceError("LOOPBACK_RESOLUTION_FAILED")
    try:
        if not all(ipaddress.ip_address(address).is_loopback for address in resolved):
            raise LiveAcceptanceError("BASE_URL_NOT_LOOPBACK")
    except ValueError as exc:
        raise LiveAcceptanceError("LOOPBACK_RESOLUTION_FAILED") from exc


def script_digest(script: Any) -> str:
    return hashlib.sha256(str(script or "").strip().encode("utf-8")).hexdigest()


def safe_count(value: Any) -> int | None:
    """Keep summary counters numeric, non-negative, and bounded."""
    if isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if 0 <= count <= MAX_EVIDENCE_COUNT else None


def safe_error_code(value: Any) -> str:
    code = str(value or "")
    return code if re.fullmatch(r"[A-Za-z0-9_]{0,96}", code) else ""


def terminal_evidence(data: dict[str, Any]) -> dict[str, Any]:
    """Return the minimal terminal evidence allowed in a shareable summary."""
    model_info = data.get("model_info") if isinstance(data.get("model_info"), dict) else {}
    tool_stats = data.get("tool_stats") if isinstance(data.get("tool_stats"), dict) else {}
    return {
        "generation_status": str(data.get("status") or ""),
        "generation_error_code": safe_error_code(data.get("error_code")),
        "model_info": {
            "provider_name": str(model_info.get("provider_name") or "")[:200],
            "model_name": str(model_info.get("model_name") or "")[:200],
            "config_id": safe_count(model_info.get("config_id")),
        },
        "calls": {
            "model_calls": safe_count(tool_stats.get("model_calls")),
            "browser_calls": safe_count(tool_stats.get("total_tool_calls")),
            "failed_calls": safe_count(tool_stats.get("failed_tool_calls")),
        },
    }


class ShortLivedTokenProvider:
    """Keep an owner JWT in memory and renew it before its ten-minute expiry."""

    def __init__(
        self, user: Any, *, mint: Callable[[Any], str], now: Callable[[], float] = time.monotonic,
        refresh_after_seconds: int = 480,
    ):
        self.user = user
        self.mint = mint
        self.now = now
        self.refresh_after_seconds = refresh_after_seconds
        self._token = ""
        self._refresh_at = 0.0

    def __call__(self) -> str:
        if not self._token or self.now() >= self._refresh_at:
            self._token = self.mint(self.user)
            self._refresh_at = self.now() + self.refresh_after_seconds
        return self._token


def response_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise LiveAcceptanceError("API_RESPONSE_NOT_SUCCESSFUL")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LiveAcceptanceError("API_RESPONSE_DATA_INVALID")
    return data


def generation_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    workspace = data.get("workspace")
    workspace = workspace if isinstance(workspace, dict) else {}
    return {
        "status": str(data.get("status") or ""),
        "stage": str(data.get("current_stage") or ""),
        "progress": data.get("progress"),
        # The model's top-level revision fences generation lifecycle work.
        # Draft/debug API mutations instead fence on workspace.revision.
        "workspace_revision": workspace.get("revision"),
        "workspace": workspace,
        "script": str(data.get("script_draft") or ""),
    }


def wait_for_terminal_generation(
    fetch: Callable[[], dict[str, Any]], *, timeout_seconds: int, poll_seconds: int,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Poll only the existing record until its generation lifecycle settles."""
    deadline = monotonic() + timeout_seconds
    previous = None
    while True:
        data = fetch()
        snapshot = generation_snapshot(data)
        compact = (snapshot["status"], snapshot["stage"], snapshot["progress"])
        if progress is not None and compact != previous:
            progress({"event": "generation", "status": compact[0], "stage": compact[1], "progress": compact[2]})
        previous = compact
        if snapshot["status"] not in ACTIVE_GENERATION_STATUSES:
            return data
        if monotonic() >= deadline:
            raise LiveAcceptanceError("GENERATION_WAIT_TIMEOUT")
        sleep(min(poll_seconds, max(0.0, deadline - monotonic())))


def ensure_eligible_draft(data: dict[str, Any]) -> tuple[int, str]:
    snapshot = generation_snapshot(data)
    if snapshot["status"] not in ELIGIBLE_GENERATION_STATUSES:
        raise LiveAcceptanceError("GENERATION_NOT_ELIGIBLE")
    if not snapshot["script"].strip():
        raise LiveAcceptanceError("DRAFT_MISSING")
    if not isinstance(snapshot["workspace_revision"], int) or snapshot["workspace_revision"] < 0:
        raise LiveAcceptanceError("WORKSPACE_REVISION_INVALID")
    verification = snapshot["workspace"].get("verification") or {}
    repair = snapshot["workspace"].get("repair") or {}
    if not isinstance(verification, dict) or not isinstance(repair, dict):
        raise LiveAcceptanceError("WORKSPACE_INVALID")
    if verification.get("status") in BUSY_STATUSES or repair.get("status") in BUSY_STATUSES:
        raise LiveAcceptanceError("WORKSPACE_BUSY")
    if repair.get("status") in {"candidate_ready", "candidate_passed"}:
        raise LiveAcceptanceError("REPAIR_CANDIDATE_PENDING")
    quality = data.get("quality_report") or {}
    if (
        not isinstance(quality, dict)
        or quality.get("status") not in {"ready", "ready_with_warnings"}
        or quality.get("completion") != "complete"
    ):
        raise LiveAcceptanceError("DRAFT_QUALITY_NOT_ELIGIBLE")
    exploration_snapshot = data.get("exploration_snapshot")
    artifact = exploration_snapshot.get("artifact") if isinstance(exploration_snapshot, dict) else None
    remaining_steps = artifact.get("remaining_steps") if isinstance(artifact, dict) else None
    if not isinstance(remaining_steps, list) or remaining_steps:
        raise LiveAcceptanceError("ARTIFACT_REMAINING_STEPS")
    return snapshot["workspace_revision"], script_digest(snapshot["script"])


def validate_debug_completion(data: dict[str, Any], *, execution_id: int, revision: int, digest: str) -> dict[str, Any]:
    snapshot = generation_snapshot(data)
    if script_digest(snapshot["script"]) != digest:
        raise LiveAcceptanceError("SCRIPT_HASH_CHANGED")
    verification = snapshot["workspace"].get("verification") or {}
    if not isinstance(verification, dict):
        raise LiveAcceptanceError("VERIFICATION_INVALID")
    if verification.get("execution_id") != execution_id:
        raise LiveAcceptanceError("VERIFICATION_EXECUTION_MISMATCH")
    if verification.get("status") in BUSY_STATUSES:
        raise LiveAcceptanceError("DEBUG_WAIT_TIMEOUT")
    if verification.get("status") != "passed":
        raise LiveAcceptanceError("VERIFICATION_NOT_PASSED")
    if verification.get("locked_revision") != revision:
        raise LiveAcceptanceError("VERIFICATION_REVISION_MISMATCH")
    if verification.get("script_hash") != digest:
        raise LiveAcceptanceError("VERIFICATION_HASH_MISMATCH")
    try:
        runtime_assertion_count = int(verification.get("runtime_assertion_count") or 0)
    except (TypeError, ValueError) as exc:
        raise LiveAcceptanceError("RUNTIME_ASSERTION_COUNT_INVALID") from exc
    if runtime_assertion_count <= 0:
        raise LiveAcceptanceError("RUNTIME_ASSERTIONS_MISSING")
    return {"verification_status": "passed", "runtime_assertion_count": runtime_assertion_count}


def validate_execution_artifacts(detail: dict[str, Any], screenshot: bytes) -> dict[str, Any]:
    if detail.get("status") != "passed":
        raise LiveAcceptanceError("EXECUTION_NOT_PASSED")
    if not isinstance(detail.get("log"), str) or not detail["log"].strip():
        raise LiveAcceptanceError("EXECUTION_LOG_MISSING")
    if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        raise LiveAcceptanceError("EXECUTION_SCREENSHOT_MISSING")
    return {"log_present": True, "screenshot_present": True, "screenshot_bytes": len(screenshot)}


class JsonHttpClient:
    """Small HTTP adapter that deliberately never includes response bodies in errors."""

    def __init__(self, base_url: str, token_provider: Callable[[], str], *, opener=loopback_open):
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.opener = opener

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any] | bytes, str]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json" if path.endswith("/") else "*/*",
                "Authorization": f"Bearer {self.token_provider()}",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                raw = response.read()
                content_type = response.headers.get_content_type()
        except HTTPError as exc:
            raise LiveAcceptanceError(f"HTTP_{exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise LiveAcceptanceError("LOCAL_HTTP_UNAVAILABLE") from exc
        if not 200 <= getattr(response, "status", 200) < 300:
            raise LiveAcceptanceError("HTTP_STATUS_UNEXPECTED")
        if content_type == "application/json":
            try:
                return json.loads(raw.decode("utf-8")), content_type
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LiveAcceptanceError("HTTP_JSON_INVALID") from exc
        return raw, content_type

    def get_json(self, path: str) -> dict[str, Any]:
        payload, _ = self._request("GET", path)
        if not isinstance(payload, dict):
            raise LiveAcceptanceError("HTTP_JSON_EXPECTED")
        return payload

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        result, _ = self._request("POST", path, payload)
        if not isinstance(result, dict):
            raise LiveAcceptanceError("HTTP_JSON_EXPECTED")
        return result

    def get_bytes(self, path: str) -> bytes:
        payload, _ = self._request("GET", path)
        if not isinstance(payload, bytes):
            raise LiveAcceptanceError("HTTP_BINARY_EXPECTED")
        return payload


def configure_django() -> None:
    sys.path[:0] = [str(BACKEND), str(BACKEND / "apps")]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    django.setup()


def owner_token_provider(generation_id: str) -> tuple[int, ShortLivedTokenProvider]:
    """Read one owner once; all short-lived JWTs stay solely in process memory."""
    configure_django()
    from rest_framework_simplejwt.tokens import AccessToken
    from web_testing.models import WebUIScriptGeneration

    try:
        generation = WebUIScriptGeneration.objects.select_related("user").get(pk=generation_id)
    except WebUIScriptGeneration.DoesNotExist as exc:
        raise LiveAcceptanceError("GENERATION_NOT_FOUND") from exc
    def mint(user: Any) -> str:
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(minutes=10))
        return str(token)

    return generation.project_id, ShortLivedTokenProvider(generation.user, mint=mint)


def generation_path(project_id: int, generation_id: str) -> str:
    return f"/api/v1/projects/{project_id}/web-testing/script-generations/{generation_id}/"


def write_summary(directory: Path, summary: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / "summary.json"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return target


def mark_debug_run_failed(run_summary: dict[str, Any], error: LiveAcceptanceError | None = None) -> None:
    """Persist only a stable local failure code, never a service response message."""
    run_summary["status"] = "failed"
    run_summary["failure_code"] = error.code if error is not None else "UNEXPECTED_LOCAL_FAILURE"


def run_live_acceptance(args: argparse.Namespace, summary: dict[str, Any]) -> int:
    ensure_resolved_loopback(args.base_url)
    project_id, token_provider = owner_token_provider(args.generation_id)
    client = JsonHttpClient(args.base_url, token_provider)
    path = generation_path(project_id, args.generation_id)
    summary.update({
        "generation_id": args.generation_id,
        "project_id": project_id,
        "debug_runs_requested": args.debug_runs,
        "completed": False,
        "debug_runs": [],
    })

    def fetch_generation() -> dict[str, Any]:
        return response_data(client.get_json(path))

    terminal = wait_for_terminal_generation(
        fetch_generation, timeout_seconds=args.wait_timeout, poll_seconds=args.poll_seconds,
        progress=lambda item: print(json.dumps(item, ensure_ascii=False), flush=True),
    )
    summary.update(terminal_evidence(terminal))
    revision, digest = ensure_eligible_draft(terminal)
    summary["initial_script_hash"] = digest

    for run_number in range(1, args.debug_runs + 1):
        current = fetch_generation()
        current_revision, current_digest = ensure_eligible_draft(current)
        if current_revision != revision:
            raise LiveAcceptanceError("REVISION_CHANGED")
        if current_digest != digest:
            raise LiveAcceptanceError("SCRIPT_HASH_CHANGED")
        debug_payload = {"expected_revision": revision, "confirm_execution": True}
        started = response_data(client.post_json(path + "debug/", debug_payload))
        verification = (started.get("workspace") or {}).get("verification") or {}
        execution_id = verification.get("execution_id")
        if not isinstance(execution_id, int):
            raise LiveAcceptanceError("DEBUG_EXECUTION_ID_MISSING")
        run_summary = {"run": run_number, "execution_id": execution_id, "status": "started"}
        summary["debug_runs"].append(run_summary)
        print(json.dumps({"event": "debug_started", "run": run_number, "execution_id": execution_id}, ensure_ascii=False), flush=True)
        try:
            deadline = time.monotonic() + args.debug_timeout
            while True:
                completed = fetch_generation()
                completed_verification = (completed.get("workspace") or {}).get("verification") or {}
                if completed_verification.get("execution_id") == execution_id and completed_verification.get("status") not in BUSY_STATUSES:
                    break
                if time.monotonic() >= deadline:
                    raise LiveAcceptanceError("DEBUG_WAIT_TIMEOUT")
                time.sleep(min(args.poll_seconds, max(0.0, deadline - time.monotonic())))
            run_summary.update(validate_debug_completion(completed, execution_id=execution_id, revision=revision, digest=digest))
            detail_path = f"/api/v1/projects/{project_id}/web-testing/executions/case/{execution_id}/"
            screenshot_path = f"/api/v1/projects/{project_id}/web-testing/executions/{execution_id}/screenshot/"
            detail = response_data(client.get_json(detail_path))
            run_summary.update(validate_execution_artifacts(detail, client.get_bytes(screenshot_path)))
            run_summary["status"] = "passed"
        except LiveAcceptanceError as exc:
            mark_debug_run_failed(run_summary, exc)
            raise
        except Exception:
            mark_debug_run_failed(run_summary)
            raise
        print(json.dumps({"event": "debug_finished", **run_summary}, ensure_ascii=False), flush=True)

    final = fetch_generation()
    if script_digest(final.get("script_draft")) != digest:
        raise LiveAcceptanceError("SCRIPT_HASH_CHANGED")
    summary["final_script_hash"] = digest
    summary["completed"] = True
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow local HTTP and approved debug execution")
    parser.add_argument("--generation-id", required=True, type=parse_generation_id)
    parser.add_argument("--debug-runs", type=bounded_integer(1, 2), default=2)
    parser.add_argument("--base-url", type=validate_loopback_base_url, default=DEFAULT_BASE_URL)
    parser.add_argument("--wait-timeout", type=bounded_integer(10, 3600), default=900)
    parser.add_argument("--debug-timeout", type=bounded_integer(10, 1800), default=300)
    parser.add_argument("--poll-seconds", type=bounded_integer(1, 60), default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # argparse does not consistently apply ``type`` to non-string defaults.
    args.base_url = validate_loopback_base_url(args.base_url)
    if not args.live:
        # This branch must stay before Django setup, model access, artifact writes and HTTP construction.
        print(json.dumps({"live": False, "generation_id": args.generation_id, "result": "dry_run_no_side_effects"}, ensure_ascii=False))
        return 0
    summary: dict[str, Any] = {"generation_id": args.generation_id, "completed": False, "debug_runs": []}
    exit_code = 1
    try:
        exit_code = run_live_acceptance(args, summary)
    except LiveAcceptanceError as exc:
        summary["failure_code"] = exc.code
    except Exception:
        # Do not persist arbitrary exception text because an upstream service may echo sensitive input.
        summary["failure_code"] = "UNEXPECTED_LOCAL_FAILURE"
    artifact = write_summary(BACKEND / "temp" / "live-acceptance" / args.generation_id, summary)
    print(json.dumps({"event": "summary", "completed": summary["completed"], "artifact": str(artifact), "failure_code": summary.get("failure_code")}, ensure_ascii=False), flush=True)
    return exit_code if summary["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
