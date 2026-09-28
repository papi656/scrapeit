"""Every knob, in one place.

scrapeit has exactly three customization surfaces:

  1. ``.env``            -- this module. LLM credentials + model choice, and the
                            exit path (endpoint URL + auth + retries).
  2. ``sources.yaml``    -- where to mine. See :mod:`scrapeit.sources`.
  3. ``data_model.yaml`` -- the shape the extraction LLM fills in.
                            See :mod:`scrapeit.datamodel`.

Nothing else needs editing to repoint the tool.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_SOURCES_PATH = "sources.yaml"
DEFAULT_DATA_MODEL_PATH = "data_model.yaml"


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class LLMConfig:
    """One OpenAI-compatible endpoint. Two roles exist: scrape and extract."""

    role: str
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class SinkConfig:
    """The exit path. If ``endpoint_url`` is None, nothing is POSTed."""

    endpoint_url: str | None
    auth_header: str
    auth_scheme: str
    auth_token: str | None
    mode: str
    max_retries: int
    timeout: float


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def llm_config(role: str) -> LLMConfig:
    """Resolve an LLM role (``"scrape"`` or ``"extract"``) from the environment.

    ``LLM_EXTRACT_*`` falls back field-by-field to ``LLM_SCRAPE_*`` so a
    single-provider setup only has to fill one block.
    """
    prefix = f"LLM_{role.upper()}_"
    base_url = _env(prefix + "BASE_URL")
    api_key = _env(prefix + "API_KEY")
    model = _env(prefix + "MODEL")

    if role != "scrape" and not (base_url and api_key and model):
        fallback = llm_config("scrape")
        base_url = base_url or fallback.base_url
        api_key = api_key or fallback.api_key
        model = model or fallback.model

    missing = [
        name
        for name, value in (
            (prefix + "BASE_URL", base_url),
            (prefix + "API_KEY", api_key),
            (prefix + "MODEL", model),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "missing environment: " + ", ".join(missing) + " (see .env.example)"
        )

    return LLMConfig(role=role, base_url=base_url.rstrip("/"), api_key=api_key, model=model)


def sink_config() -> SinkConfig:
    """Resolve the exit path from the environment."""
    mode = (_env("SINK_MODE", "batch") or "batch").lower()
    if mode not in ("batch", "item"):
        raise ConfigError(f"SINK_MODE must be 'batch' or 'item', got {mode!r}")

    try:
        max_retries = int(_env("SINK_MAX_RETRIES", "3"))
    except ValueError:
        raise ConfigError("SINK_MAX_RETRIES must be an integer") from None
    try:
        timeout = float(_env("SINK_TIMEOUT", "30"))
    except ValueError:
        raise ConfigError("SINK_TIMEOUT must be a number") from None

    return SinkConfig(
        endpoint_url=_env("SINK_ENDPOINT_URL") or None,
        auth_header=_env("SINK_AUTH_HEADER", "Authorization") or "Authorization",
        auth_scheme=_env("SINK_AUTH_SCHEME", "Bearer"),
        auth_token=_env("SINK_AUTH_TOKEN") or None,
        mode=mode,
        max_retries=max_retries,
        timeout=timeout,
    )
