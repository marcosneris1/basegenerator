"""Databricks execution layer for Base Generator.

Runs the generated Scala **interactively** on an existing cluster using the
Command Execution API (execution contexts) — the same mechanism a notebook cell
uses. This works on interactive-only clusters (jobs workload disabled), unlike
the Jobs API.

Flow:
  1. Open an execution context (Scala) on the cluster.
  2. Split the generated notebook into cells; inline any `%run` helper notebook
     by fetching its source and running its code cells first (so `datasets()`,
     `.save()`, `maximo`, … are defined in the context).
  3. Run each code cell in order — the context persists `val`s across cells.
  4. The trailing CSV-export cells write each table to the UC Volume; then we
     locate and download those files.

Auth uses the Databricks SDK default chain: injected service-principal
credentials when deployed as a Databricks App, or your local profile otherwise.
The SDK is imported lazily so the rest of the app still runs without it.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Callable

_CMD_SEP = "// COMMAND ----------"


@dataclass
class RunResult:
    """Outcome of one interactive run."""

    state: str  # "SUCCESS" | "FAILED" | "ERROR"
    message: str = ""
    csv_files: dict[str, str] = field(default_factory=dict)  # table -> Volume CSV path
    csv_sizes: dict[str, int] = field(default_factory=dict)  # table -> size in bytes

    @property
    def ok(self) -> bool:
        return self.state == "SUCCESS"


def sdk_available() -> bool:
    """True when `databricks-sdk` is importable in the current environment."""
    import importlib.util

    try:
        return importlib.util.find_spec("databricks.sdk") is not None
    except ModuleNotFoundError:
        return False


def is_deployed() -> bool:
    """True when running inside a Databricks App (SP env vars are injected)."""
    import os

    return bool(
        os.getenv("DATABRICKS_CLIENT_ID")
        or os.getenv("DATABRICKS_APP_NAME")
        or os.getenv("DATABRICKS_APP_PORT")
    )


def _client(user_token: str | None = None):
    """Build a WorkspaceClient with the right identity for the environment.

    - **Deployed app + `user_token`** (on-behalf-of-user): authenticate AS the
      logged-in user via their forwarded access token, so Unity Catalog / cluster
      permissions match what the user has (no PII access granted to the app's
      service principal). The token must be read fresh per request, never cached.
    - **Deployed app, no token**: fall back to the injected service principal.
    - **Local dev**: pin the CLI profile so the SDK does NOT walk the whole
      default auth chain, which can stall for minutes probing cloud-metadata
      endpoints before falling back to the profile. Override with
      `DATABRICKS_CONFIG_PROFILE`.
    """
    import os

    from databricks.sdk import WorkspaceClient

    if user_token:
        host = os.getenv("DATABRICKS_HOST")
        return WorkspaceClient(host=host, token=user_token)

    if is_deployed():
        return WorkspaceClient()

    profile = os.getenv("DATABRICKS_CONFIG_PROFILE") or "Marcos Neris"
    try:
        return WorkspaceClient(profile=profile)
    except Exception:
        # Profile missing/misconfigured — fall back to the default chain.
        return WorkspaceClient()


def list_clusters(user_token: str | None = None) -> list[tuple[str, str, str]]:
    """Return (name, id, state) for clusters visible to the current identity.

    Interactive execution needs the cluster **running**, so `state` is the
    useful signal here (jobs workload no longer matters).
    """
    w = _client(user_token)
    out: list[tuple[str, str, str]] = []
    for c in w.clusters.list():
        state = c.state.value if c.state else ""
        out.append((c.cluster_name or "(unnamed)", c.cluster_id or "", state))
    return out


def _resolve_cluster_id(w, value: str) -> str:
    """Accept a cluster ID *or* display name and return the cluster ID."""
    value = value.strip()
    try:
        info = w.clusters.get(cluster_id=value)
        if info and info.cluster_id:
            return info.cluster_id
    except Exception:
        pass

    clusters = list(w.clusters.list())
    matches = [c for c in clusters if (c.cluster_name or "").strip() == value]
    if len(matches) == 1:
        return matches[0].cluster_id or value
    if len(matches) > 1:
        opts = ", ".join(f"{c.cluster_name} ({c.cluster_id})" for c in matches)
        raise ValueError(
            f"Multiple clusters are named '{value}' — use the cluster ID. "
            f"Options: {opts}"
        )
    names = sorted({(c.cluster_name or "").strip() for c in clusters if c.cluster_name})
    sample = ", ".join(names[:15]) if names else "(none visible to this identity)"
    raise ValueError(
        f"No cluster matched ID or name '{value}'. Available names: {sample}"
    )


# --- notebook parsing / %run inlining ------------------------------------


def _iter_cells(source: str):
    """Yield raw cell text blocks split on the `// COMMAND ----------` marker."""
    cell: list[str] = []
    for line in source.splitlines():
        if line.strip() == _CMD_SEP:
            yield "\n".join(cell)
            cell = []
        else:
            cell.append(line)
    if cell:
        yield "\n".join(cell)


def _classify_cell(cell: str) -> tuple[str, str]:
    """Return (kind, payload) for a cell.

    kind ∈ {"run", "md", "code", "empty"}:
      - "run"  → payload is the `%run` target notebook path
      - "md"   → markdown cell (skip)
      - "code" → payload is executable Scala (MAGIC/header lines stripped)
      - "empty"→ nothing to run
    """
    run_path: str | None = None
    is_md = False
    code_lines: list[str] = []
    for line in cell.splitlines():
        s = line.strip()
        if s == "// Databricks notebook source":
            continue
        if s.startswith("// MAGIC"):
            content = s[len("// MAGIC"):].strip()
            if content.startswith("%run"):
                run_path = content[len("%run"):].strip()
            elif content.startswith("%md"):
                is_md = True
            # other magics (e.g. blank continuation) ignored
            continue
        code_lines.append(line)

    if run_path:
        return "run", run_path
    if is_md:
        return "md", ""
    code = "\n".join(code_lines).strip()
    if not code:
        return "empty", ""
    return "code", code


def _is_display_cell(code: str) -> bool:
    """True for the generator's `table("x") … .d` display cells (skip them)."""
    c = code.strip()
    return c.startswith("table(") and c.rstrip().endswith(".d")


def _is_csv_export_cell(code: str) -> bool:
    """True for the trailing CSV-export cells (`.write…​.csv(...)`)."""
    low = code.lower()
    return ".write" in low and ".csv(" in low


def _fmt_secs(s: float) -> str:
    """Human-friendly elapsed time, e.g. `42.1s` or `3m07s`."""
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s"


def _fetch_notebook_source(w, path: str) -> str:
    """Export a workspace notebook as SOURCE text (for `%run` inlining)."""
    from databricks.sdk.service.workspace import ExportFormat

    resp = w.workspace.export(path=path, format=ExportFormat.SOURCE)
    return base64.b64decode(resp.content).decode("utf-8")


def _build_chunks(w, source: str, depth: int = 0) -> list[str]:
    """Flatten a notebook into ordered executable Scala chunks.

    `%run` cells are replaced by the referenced notebook's code chunks
    (recursively, with a small depth guard). Markdown, display, and empty cells
    are dropped.
    """
    chunks: list[str] = []
    for cell in _iter_cells(source):
        kind, payload = _classify_cell(cell)
        if kind == "run":
            if depth < 3:
                helper = _fetch_notebook_source(w, payload)
                chunks.extend(_build_chunks(w, helper, depth + 1))
        elif kind == "code" and not _is_display_cell(payload):
            chunks.append(payload)
    return chunks


# --- CSV location / download ---------------------------------------------


def _fmt_size(n: int | None) -> str:
    """Human-friendly byte size, e.g. `842.0 KB` or `12.3 MB`."""
    if not n:
        return "unknown size"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _find_csv_in_dir(w, csv_dir: str) -> tuple[str, int | None] | None:
    """Return `(path, size_bytes)` for the single part-file CSV in a Spark dir."""
    try:
        entries = list(w.files.list_directory_contents(csv_dir))
    except Exception:
        return None
    for e in entries:
        path = getattr(e, "path", "") or ""
        is_dir = getattr(e, "is_directory", False)
        if not is_dir and path.lower().endswith(".csv"):
            return path, getattr(e, "file_size", None)
    return None


def download_csv(file_path: str, user_token: str | None = None) -> bytes:
    """Download a CSV file from a UC Volume, retrying transient blips."""
    w = _client(user_token)
    transient = _transient_exc_types()
    last: Exception | None = None
    for attempt in range(5):
        try:
            resp = w.files.download(file_path)
            return resp.contents.read()
        except transient as e:  # 504/429/connection reset on the Files API
            last = e
            time.sleep(min(15.0, 3.0 * (attempt + 1)))
    raise last or RuntimeError(f"Failed to download {file_path}")


# --- interactive run ------------------------------------------------------


def _result_is_error(results) -> bool:
    rt = getattr(results, "result_type", None)
    val = getattr(rt, "value", None) or str(rt)
    return str(val).lower() == "error"


def _error_detail(results) -> str:
    """Build a concise, actionable message from a failed command's results.

    Command errors carry a short `summary` and a long `cause` (full JVM stack).
    Show the summary (or the first few stack lines) and add a targeted hint for
    common failures like UC Volume credential/permission errors.
    """
    summary = (getattr(results, "summary", None) or "").strip()
    cause = (getattr(results, "cause", None) or "").strip()

    short = summary or "\n".join(cause.splitlines()[:3])
    short = short.strip() or "unknown error"
    if len(short) > 600:
        short = short[:600] + " …"

    low = (summary + " " + cause).lower()
    hint = ""
    if (
        "generatetemporaryvolumecredentials" in low
        or "volumesam" in low
        or "temporary credentials" in low
    ):
        hint = (
            "\n\n→ Writing to the UC Volume was denied. Verify the Volume path "
            "exists (`/Volumes/<catalog>/<schema>/<volume>/…`) and that the running "
            "identity has **WRITE VOLUME** (and READ VOLUME) on it."
        )
    elif "permission_denied" in low or "does not have" in low:
        hint = "\n\n→ Looks like a permission grant is missing for the running identity."
    return short + hint


def _transient_exc_types() -> tuple[type, ...]:
    """Exception types that mean 'the poll request blipped — just retry'.

    A heavy cell keeps the cluster busy long enough that the `/commands/status`
    endpoint occasionally returns 504 (`DeadlineExceeded`), 429, or a transient
    5xx / connection reset. None of those mean the *command* failed, so we
    swallow them and poll again rather than killing the whole run.
    """
    types: list[type] = [TimeoutError]
    try:
        from databricks.sdk.errors import platform as _p

        for n in (
            "DeadlineExceeded",
            "TemporarilyUnavailable",
            "TooManyRequests",
            "InternalError",
            "BadGateway",
        ):
            t = getattr(_p, n, None)
            if isinstance(t, type):
                types.append(t)
    except Exception:
        pass
    try:
        import requests

        types.extend([requests.exceptions.ConnectionError, requests.exceptions.Timeout])
    except Exception:
        pass
    return tuple(types)


def _execute_cell(w, cid: str, ctx_id: str, code, *, deadline: float, poll_s: float = 3.0):
    """Run one command and poll to completion, tolerating transient poll errors.

    Returns the command's `results` object. Raises `TimeoutError` if the cell
    is still running past `deadline` (monotonic seconds).
    """
    from databricks.sdk.service.compute import Language

    transient = _transient_exc_types()

    waiter = w.command_execution.execute(
        cluster_id=cid, context_id=ctx_id, language=Language.SCALA, command=code
    )
    command_id = waiter.command_id
    misses = 0
    while True:
        if time.monotonic() > deadline:
            try:
                w.command_execution.cancel(
                    cluster_id=cid, context_id=ctx_id, command_id=command_id
                )
            except Exception:
                pass
            raise TimeoutError("cell exceeded the run timeout")
        try:
            resp = w.command_execution.command_status(
                cluster_id=cid, context_id=ctx_id, command_id=command_id
            )
            misses = 0
        except transient:
            # Poll blip (e.g. 504 while the cell is heavy) — back off and retry.
            misses += 1
            time.sleep(min(15.0, poll_s * misses))
            continue
        status = resp.status
        name = getattr(status, "value", None) or str(status)
        if name in ("Finished", "Error", "Cancelled"):
            return getattr(resp, "results", None)
        time.sleep(poll_s)


def run_interactive(
    scala_source: str,
    table_names: list[str],
    *,
    cluster_id: str,
    volume_dir: str,
    progress: Callable[[str], None] | None = None,
    timeout_min: int = 60,
    user_token: str | None = None,
) -> RunResult:
    """Run the generated Scala interactively on `cluster_id` and export CSVs.

    `progress(msg)` — optional callback for UI updates (per-cell status).
    `user_token` — forwarded user access token for on-behalf-of-user auth in the
    deployed app; when set, the run executes with the user's identity.
    """
    from databricks.sdk.service.compute import Language

    def _say(msg: str) -> None:
        # Wall-clock timestamp so any local suspension (e.g. the laptop sleeping,
        # which freezes the poll loop) shows up as a visible jump between lines.
        if progress:
            progress(f"[{time.strftime('%H:%M:%S')}] {msg}")

    if is_deployed() and not user_token:
        return RunResult(
            "ERROR",
            "This deployed app needs **user authorization** (on-behalf-of-user) "
            "to run as you. No forwarded user token was found.\n\n"
            "→ An admin must enable it: App **settings → Authorization → User "
            "authorization**, add scopes for compute + files, then **fully stop "
            "and start** the app (a redeploy alone doesn't apply it).",
        )

    _say("Authenticating to Databricks…")
    t_auth = time.time()
    try:
        w = _client(user_token)
    except Exception as e:
        return RunResult("ERROR", f"Could not authenticate to Databricks: {e}")
    _say(f"  authenticated in {_fmt_secs(time.time() - t_auth)}")

    _say(f"Resolving cluster '{cluster_id}'…")
    t_res = time.time()
    try:
        cid = _resolve_cluster_id(w, cluster_id)
    except Exception as e:
        return RunResult("ERROR", str(e))
    _say(f"  resolved cluster in {_fmt_secs(time.time() - t_res)}")

    _say("Preparing code (inlining %run helpers)…")
    t_prep = time.time()
    try:
        chunks = _build_chunks(w, scala_source)
    except Exception as e:
        return RunResult("ERROR", f"Failed to prepare code (%run inlining): {e}")
    if not chunks:
        return RunResult("ERROR", "Nothing to run — no code cells found.")
    _say(f"  prepared {len(chunks)} cells in {_fmt_secs(time.time() - t_prep)}")

    _say(f"Opening execution context on {cid} (starts the cluster if cold)…")
    t_ctx = time.time()
    try:
        ctx = w.command_execution.create_and_wait(
            cluster_id=cid, language=Language.SCALA
        )
    except Exception as e:
        return RunResult(
            "ERROR",
            f"Could not open an execution context: {e}. "
            "The cluster must be RUNNING.",
        )
    _say(f"  context ready in {_fmt_secs(time.time() - t_ctx)}")

    try:
        total = len(chunks)
        run_started = time.time()
        for i, code in enumerate(chunks, 1):
            tag = " (CSV export)" if _is_csv_export_cell(code) else ""
            _say(f"▶ Running cell {i}/{total}{tag}…")
            t0 = time.time()
            try:
                results = _execute_cell(
                    w,
                    cid,
                    ctx.id,
                    code,
                    deadline=time.monotonic() + timeout_min * 60,
                )
            except TimeoutError:
                dt = time.time() - t0
                return RunResult(
                    "ERROR",
                    f"Cell {i}/{total} was still running after {_fmt_secs(dt)} "
                    f"(timeout {timeout_min} min). Increase the timeout or use a "
                    "bigger cluster / tighter filters.",
                )
            dt = time.time() - t0
            if results is not None and _result_is_error(results):
                return RunResult(
                    "FAILED",
                    f"Cell {i}/{total} failed after {_fmt_secs(dt)}:\n"
                    f"{_error_detail(results)}",
                )
            total_elapsed = time.time() - run_started
            _say(
                f"✓ cell {i}/{total}{tag} — {_fmt_secs(dt)} "
                f"(elapsed {_fmt_secs(total_elapsed)})"
            )

        _say(
            f"Run finished — cell compute {_fmt_secs(time.time() - run_started)} "
            "wall-clock. Locating CSV(s) in the Volume…"
        )
        out = RunResult("SUCCESS")
        vol = volume_dir.rstrip("/")
        for name in table_names:
            t_loc = time.time()
            found = _find_csv_in_dir(w, f"{vol}/{name}")
            dt = _fmt_secs(time.time() - t_loc)
            if found:
                path, size = found
                out.csv_files[name] = path
                out.csv_sizes[name] = size or 0
                _say(f"  {name}: found ({_fmt_size(size)}) in {dt}")
            else:
                _say(f"  {name}: no CSV found under {vol}/{name} ({dt})")
        return out
    finally:
        # Best-effort, time-boxed cleanup. `destroy` has been observed to hang
        # for a long time (network stall / laptop sleeping while blocked on it),
        # and it must NOT delay returning the result. Execution contexts expire
        # on their own, so if the destroy doesn't finish quickly we just move on.
        import threading

        _say("Cleaning up execution context…")
        t_cl = time.time()
        done = threading.Event()

        def _destroy():
            try:
                w.command_execution.destroy(cluster_id=cid, context_id=ctx.id)
            except Exception:
                pass
            finally:
                done.set()

        threading.Thread(target=_destroy, daemon=True).start()
        if done.wait(timeout=20):
            _say(f"  cleanup done in {_fmt_secs(time.time() - t_cl)}")
        else:
            _say(
                "  cleanup still running after 20s — moving on "
                "(the context expires on its own)."
            )
