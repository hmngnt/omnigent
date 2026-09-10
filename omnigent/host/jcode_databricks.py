"""Point a managed sandbox's jcode gateway at the owner's Databricks model-serving endpoint.

Called by ``omnigent host`` at startup when the owner has linked a Databricks workspace
via the OAuth U2M connect flow. Mirrors the existing ucode/claude/codex/pi connect-broker
pattern (see :mod:`omnigent.host.databricks_credential`): configures jcode's openai-compatible
provider to route inference through the owner's workspace gateway, using a fresh bearer
token minted on demand (never persisted).

When the managed-connect signals are absent (no ``[omnigent]`` profile + broker sidecar),
or jcode is not installed, this is a complete no-op — laptops and non-connected sandboxes
are unaffected.

The configured provider id is ``"dbx"``; jcode reads ``~/.jcode/config.toml`` and runs
its daemon per-session (a unique ``JCODE_RUNTIME_DIR`` per spawn), so the fresh bearer
and model are loaded at startup and never persisted.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading

from omnigent.host.databricks_credential import (
    HOST_DATABRICKS_PROFILE,
    _read_sidecar,
    _sidecar_path,
    broker_token_command,
    fetch_broker_bearer,
)
from omnigent.inner.databricks_executor import _read_databrickscfg_host

_logger = logging.getLogger(__name__)

# Sandbox configure is best-effort and off the host's dial-back path; bound it so
# a hung jcode can't leak a thread for the life of the host.
_SANDBOX_CONFIGURE_TIMEOUT_S = 120

# jcode's provider id for the Databricks gateway.
_JCODE_PROVIDER_ID = "dbx"

# Environment variable names jcode's gateway bearer is exported under.
_JCODE_BEARER_ENV = "JCODE_DBX_TOKEN"
_JCODE_RUNTIME_DIR_ENV = "JCODE_RUNTIME_DIR"

# Default served model when not overridden by env or config.
_JCODE_DATABRICKS_DEFAULT_MODEL = "system.ai.claude-sonnet-4-6"

# Environment variable for model override (mirrors claude-native and opencode).
_JCODE_DATABRICKS_GATEWAY_MODEL_ENV = "OMNIGENT_DATABRICKS_GATEWAY_MODEL"


def build_jcode_configure_command(
    jcode_command: list[str],
    *,
    host: str,
    model: str,
) -> list[str]:
    """Build the ``jcode provider add dbx`` command Omnigent runs.

    :param jcode_command: Command prefix that invokes jcode, e.g.
        ``["/usr/bin/jcode"]`` or ``["jcode"]``.
    :param host: The Databricks workspace host, e.g.
        ``"https://example.databricks.com"``. The base URL is constructed as
        ``{host}/ai-gateway/openai/v1``.
    :param model: The served model to configure, e.g.
        ``"system.ai.claude-sonnet-4-6"``. Must be non-empty.
    :returns: Command argv using jcode's ``provider add`` with openai-compatible
        config.
    :raises ValueError: If *host* or *model* is empty.
    """
    if not host or not model:
        raise ValueError("host and model must not be empty")
    base_url = f"{host.rstrip('/')}/ai-gateway/openai/v1"
    return [
        *jcode_command,
        "provider",
        "add",
        _JCODE_PROVIDER_ID,
        "--base-url",
        base_url,
        "--model",
        model,
        "--auth",
        "bearer",
        "--api-key-env",
        _JCODE_BEARER_ENV,
        "--set-default",
        "--overwrite",
        "--quiet",
    ]


def configure_jcode_for_sandbox() -> None:
    """Populate jcode's gateway provider at managed-sandbox host boot, in the background.

    Configures jcode's openai-compatible provider to route inference through the owner's
    Databricks model-serving gateway when a managed-connect sidecar signals that the owner
    has connected (host-only ``[omnigent]`` profile + broker coordinates on disk).

    A daemon thread keeps this off ``omnigent host``'s dial-back path, where a synchronous
    multi-second configure would delay the runner connect. The jcode daemon is spawned per
    session with a fresh bearer and a unique runtime dir, so tokens are never persisted.
    Best-effort: when jcode isn't available or the managed-connect gate isn't satisfied,
    silently no-ops rather than raising.

    This mirrors :func:`omnigent.onboarding.ucode_setup.configure_ucode_for_sandbox` in
    scope (host-boot only, daemon thread, best-effort), but runs with a fresh bearer passed
    in the spawn env rather than the global ``DATABRICKS_BEARER_COMMAND``.
    """
    # Managed-connect gate: workspace host from profile + broker command present.
    workspace = _read_databrickscfg_host(HOST_DATABRICKS_PROFILE)
    if not workspace:
        return
    bearer_command = broker_token_command(workspace)
    if not bearer_command:
        return  # no broker sidecar → not a managed connect host

    # Find jcode binary (best-effort; if absent, no-op).
    jcode_bin = shutil.which("jcode")
    if jcode_bin is None:
        _logger.debug("jcode: binary not found on PATH")
        return

    # Resolve the served model. Honor OMNIGENT_DATABRICKS_GATEWAY_MODEL only when it
    # names a ``system.ai.*`` model: jcode's openai-compatible path serves the
    # ``system.ai`` namespace, whereas that env may carry a ``databricks-*``
    # serving-endpoint id (opencode's /serving-endpoints style) that this path rejects.
    override = os.environ.get(_JCODE_DATABRICKS_GATEWAY_MODEL_ENV, "").strip()
    model = override if override.startswith("system.ai.") else _JCODE_DATABRICKS_DEFAULT_MODEL

    argv = build_jcode_configure_command([jcode_bin], host=workspace, model=model)

    # ``jcode provider add`` only writes config.toml (no network, no token needed) —
    # the bearer is minted at spawn time by connect_jcode_gateway_env. Pass a minimal
    # env: suppress telemetry and withhold the host launch token.
    env = {**os.environ, "JCODE_NO_TELEMETRY": "1"}
    env.pop("OMNIGENT_HOST_TOKEN", None)

    def _run() -> None:
        try:
            result = subprocess.run(
                argv, capture_output=True, timeout=_SANDBOX_CONFIGURE_TIMEOUT_S, env=env
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # A failed/timed-out configure is best-effort; the spawn-time
            # connect_jcode_gateway_env() will mint a fresh bearer, so the jcode
            # daemon can still run. Log at WARNING so the field isn't blind.
            _logger.warning("jcode: sandbox configure failed: %r", exc)
            return
        if result.returncode != 0:
            # Log the returncode, not stderr — stderr could echo a secret.
            _logger.warning("jcode: sandbox configure exit=%s", result.returncode)
        else:
            _logger.info("jcode: sandbox configure ok")

    threading.Thread(target=_run, name="jcode-configure", daemon=True).start()


def connect_jcode_gateway_env() -> dict[str, str] | None:
    """Mint a fresh Databricks bearer and runtime dir for a jcode spawn.

    Called at spawn time (not host boot) to inject per-session jcode gateway
    configuration. Reads the managed-connect sidecar; if present and valid, fetches
    a fresh bearer via the broker and returns the two env vars jcode needs
    (``JCODE_DBX_TOKEN``, ``JCODE_RUNTIME_DIR``).

    The runtime dir is created with 0700 permissions to isolate the session's daemon
    and config; it's temporary and cleaned up when the runner exits.

    Returns ``None`` when:
    - The sidecar is absent (not a managed-connect host), or
    - The broker is unreachable or declines, or
    - Any error occurs reading the sidecar.

    Best-effort: a ``None`` return makes the spawn a complete no-op (jcode uses its
    existing config), so non-connect launches are untouched. Errors are logged but
    never raised.

    :returns: A dict with keys ``JCODE_DBX_TOKEN`` and ``JCODE_RUNTIME_DIR``, or
        ``None`` when the managed-connect gate is not satisfied.
    """
    coords = _read_sidecar(_sidecar_path())
    if coords is None:
        return None

    try:
        resolved = fetch_broker_bearer(coords["server"], coords["host_id"], coords["host_token"])
    except Exception as exc:  # noqa: BLE001
        # Catch all exceptions: httpx errors, dict access errors, etc.
        # This is best-effort code; any broker failure is a no-op.
        _logger.debug("jcode: broker fetch failed: %r", exc)
        return None

    if resolved is None:
        return None

    workspace_host, bearer = resolved
    # Reconnect guard: if the broker now vends a different workspace than the sidecar
    # pins (the owner reconnected elsewhere), don't forward this bearer to jcode's
    # config, which is pinned to the sidecar workspace. Mirrors databricks_credential.main.
    if workspace_host.rstrip("/") != coords["workspace_host"].rstrip("/"):
        return None

    # Create a unique runtime dir for this spawn (0700 isolation).
    try:
        runtime_dir = tempfile.mkdtemp(prefix="omnigent-jcode-run-")
        os.chmod(runtime_dir, 0o700)
    except OSError as exc:
        _logger.warning("jcode: could not create runtime dir: %r", exc)
        return None

    return {
        _JCODE_BEARER_ENV: bearer,
        _JCODE_RUNTIME_DIR_ENV: runtime_dir,
    }
