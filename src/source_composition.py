"""Safe composition for configurable sources; no credentials are persisted."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .business_semantics import LocalFileSourceConfigRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / ".agents" / "skills" / "simple-visual-compliance" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from feishu_openapi_adapter import FeishuOpenApiAdapter


@dataclass(frozen=True)
class ConfigPersistence:
    path: Path | None
    mode: str
    backend: str = "local_file"


def config_persistence() -> ConfigPersistence:
    # The only implemented adapter is a local filesystem repository.  Vercel's
    # filesystem is request-local/ephemeral, so a directory variable must never
    # be interpreted as durable configuration storage there.  A future durable
    # Vercel option needs a distinct SourceConfigRepository backend, not a path.
    if os.environ.get("VERCEL"):
        return ConfigPersistence(Path("/tmp/ecom-source-config"), "ephemeral", "vercel_ephemeral_local_file")
    configured = os.environ.get("SOURCE_CONFIG_DIR", "").strip()
    if configured:
        return ConfigPersistence(Path(configured), "persistent", "local_file")
    return ConfigPersistence(PROJECT_ROOT / "runtime" / "source_configs", "persistent", "local_file")


def config_repository() -> tuple[LocalFileSourceConfigRepository | None, ConfigPersistence]:
    persistence = config_persistence()
    try:
        if persistence.path is None:
            return None, ConfigPersistence(None, "unavailable", "local_file")
        persistence.path.mkdir(parents=True, exist_ok=True)
        return LocalFileSourceConfigRepository(persistence.path), persistence
    except OSError:
        return None, ConfigPersistence(None, "unavailable", "local_file")


def resolve_feishu_credential(reference: str) -> FeishuOpenApiAdapter:
    """Resolve a restricted env-var reference without exposing secret material."""
    ref = str(reference or "").strip()
    if ref == "FEISHU_PRIMARY_APP":
        app_id = os.environ.get("FEISHU_APP_ID", "").strip()
        secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    else:
        if not re.fullmatch(r"FEISHU_CREDENTIAL_[A-Z0-9_]{1,40}", ref):
            raise ValueError("credential_ref 不受支持")
        app_id = os.environ.get(f"{ref}_APP_ID", "").strip()
        secret = os.environ.get(f"{ref}_APP_SECRET", "").strip()
    if not app_id or not secret:
        # This is deployment state, not an invalid client payload.  Keep secret
        # values out of the exception because web_api returns a sanitized 503.
        raise RuntimeError("飞书应用身份当前不可用")
    return FeishuOpenApiAdapter(app_id, secret)
