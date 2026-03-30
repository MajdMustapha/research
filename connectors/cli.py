"""
polymarket-cli subprocess wrapper.
All Polymarket I/O goes through this module — no py-clob-client.
Includes private key log scrubber (Section 14n).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess

logger = logging.getLogger(__name__)

_KEY_PATTERN = re.compile(r"0x[0-9a-fA-F]{40,}")


class SensitiveFilter(logging.Filter):
    """Redacts private keys and long hex strings from all log output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _KEY_PATTERN.sub("[REDACTED]", record.msg)
        if record.args:
            record.args = tuple(
                _KEY_PATTERN.sub("[REDACTED]", str(a)) if isinstance(a, str) else a
                for a in (
                    record.args if isinstance(record.args, tuple) else (record.args,)
                )
            )
        return True


def setup_logging() -> None:
    """Call once at startup from main.py."""
    handler = logging.StreamHandler()
    handler.addFilter(SensitiveFilter())
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)


def cli(args: str, timeout: int = 15) -> dict | list:
    """
    Run a polymarket-cli command and return parsed JSON.
    Raises RuntimeError on non-zero exit or parse failure.
    Always uses -o json flag.
    """
    cmd = f"polymarket -o json {args}"
    env = {**os.environ}
    pk = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if pk:
        env["POLYMARKET_PRIVATE_KEY"] = pk

    try:
        result = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"CLI timeout after {timeout}s: polymarket {args}")
    except FileNotFoundError:
        raise RuntimeError(
            "polymarket-cli not found. Install via: "
            "curl -sSL https://raw.githubusercontent.com/Polymarket/polymarket-cli/main/install.sh | sh"
        )

    safe_out = _KEY_PATTERN.sub("[REDACTED]", result.stdout)
    safe_err = _KEY_PATTERN.sub("[REDACTED]", result.stderr)

    if result.returncode != 0:
        raise RuntimeError(f"CLI error (rc={result.returncode}): {safe_out} | {safe_err}")

    logger.debug("CLI stdout: %s", safe_out[:500])

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"CLI JSON parse error: {exc} | raw: {safe_out[:200]}")
