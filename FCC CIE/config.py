"""
Configuration module for Flask application.

Loads application settings in priority order:
1. AWS SSM Parameter Store under '/portfolio/' using boto3 when env var USE_SSM=true
2. Local .env file via python-dotenv

All required keys are validated at startup (fail fast with a clear error if missing).
Values are cached in memory at startup to prevent repeated network or disk I/O.
No secrets are hardcoded or logged.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Required configuration keys specified by system requirements
REQUIRED_KEYS: Tuple[str, ...] = (
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "SECRET_KEY",
    "DATABASE_URL",
    "S3_BUCKET",
    "AWS_REGION",
    "ALLOWED_ORIGIN",
    "PORT",
)

# Sensitive keys that must NEVER be exposed in logs or string representations
SECRET_KEYS = frozenset({"ADMIN_PASSWORD", "SECRET_KEY", "DATABASE_URL"})


def _load_from_ssm(region: Optional[str] = None) -> Dict[str, str]:
    """
    Fetch configuration settings from AWS SSM Parameter Store under /portfolio/.
    Uses WithDecryption=True for SecureString parameters.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required to load configuration from AWS SSM Parameter Store when USE_SSM=true. "
            "Please install boto3 via requirements.txt."
        ) from exc

    client_kwargs: Dict[str, Any] = {}
    if region:
        client_kwargs["region_name"] = region

    try:
        ssm_client = boto3.client("ssm", **client_kwargs)
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize AWS SSM client: {exc}") from exc

    loaded: Dict[str, str] = {}
    path_prefix = "/portfolio/"

    try:
        paginator = ssm_client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(
            Path=path_prefix,
            Recursive=True,
            WithDecryption=True,
        ):
            for param in page.get("Parameters", []):
                name = param.get("Name", "")
                if name.startswith(path_prefix):
                    key = name[len(path_prefix):]
                elif name.startswith("/portfolio"):
                    key = name[len("/portfolio"):].lstrip("/")
                else:
                    key = name.split("/")[-1]

                val = param.get("Value")
                if key and val is not None:
                    loaded[key] = val
    except Exception as exc:
        raise RuntimeError(
            f"Failed to fetch parameters from AWS SSM Parameter Store under '{path_prefix}': {exc}"
        ) from exc

    # Fallback to direct get_parameter for any missing required keys
    for req_key in REQUIRED_KEYS:
        if req_key not in loaded or not loaded[req_key]:
            param_name = f"/portfolio/{req_key}"
            try:
                resp = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
                val = resp.get("Parameter", {}).get("Value")
                if val:
                    loaded[req_key] = val
            except (BotoCoreError, ClientError):
                pass

    return loaded


def _load_from_dotenv() -> Dict[str, str]:
    """
    Load configuration settings from a local .env file via python-dotenv.
    """
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "python-dotenv is required to load settings from a local .env file. "
            "Please install python-dotenv via requirements.txt."
        ) from exc

    base_dir = Path(__file__).resolve().parent
    env_path = base_dir / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    loaded: Dict[str, str] = {}
    for key in REQUIRED_KEYS:
        val = os.getenv(key)
        if val is not None and val.strip() != "":
            loaded[key] = val.strip()

    return loaded


def _load_and_validate_config() -> Dict[str, Any]:
    """
    Load settings according to priority order:
    1. AWS SSM Parameter Store under /portfolio/ if USE_SSM=true
    2. Local .env via python-dotenv otherwise

    Validates all required keys are present and non-empty (fail-fast).
    Validates PORT is a valid integer port number.
    Returns cleaned configuration dictionary.
    """
    raw_use_ssm = os.getenv("USE_SSM", "").strip().lower()
    use_ssm = raw_use_ssm in ("true", "1", "yes")

    if use_ssm:
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        raw_settings = _load_from_ssm(region=region)
        source_desc = "AWS SSM Parameter Store under /portfolio/"
    else:
        raw_settings = _load_from_dotenv()
        source_desc = "local .env file"

    # Fail fast if any required key is missing or blank
    missing_keys = [
        key
        for key in REQUIRED_KEYS
        if key not in raw_settings or raw_settings[key] is None or str(raw_settings[key]).strip() == ""
    ]

    if missing_keys:
        raise RuntimeError(
            f"Missing required configuration key(s) from {source_desc}: "
            f"{', '.join(sorted(missing_keys))}"
        )

    # Validate and convert PORT
    raw_port = raw_settings["PORT"]
    try:
        port_num = int(raw_port)
        if not (1 <= port_num <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {port_num}")
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"Invalid PORT value '{raw_port}': {exc}") from exc

    storage_backend = str(
        raw_settings.get("STORAGE_BACKEND", os.getenv("STORAGE_BACKEND", "local"))
    ).strip().lower()
    if storage_backend not in ("local", "s3"):
        raise RuntimeError("STORAGE_BACKEND must be either 'local' or 's3'")

    cleaned: Dict[str, Any] = {
        "ADMIN_USERNAME": str(raw_settings["ADMIN_USERNAME"]).strip(),
        "ADMIN_PASSWORD": str(raw_settings["ADMIN_PASSWORD"]),
        "SECRET_KEY": str(raw_settings["SECRET_KEY"]),
        "DATABASE_URL": str(raw_settings["DATABASE_URL"]).strip(),
        "S3_BUCKET": str(raw_settings["S3_BUCKET"]).strip(),
        "AWS_REGION": str(raw_settings["AWS_REGION"]).strip(),
        "ALLOWED_ORIGIN": str(raw_settings["ALLOWED_ORIGIN"]).strip(),
        "PORT": port_num,
        "STORAGE_BACKEND": storage_backend,
        "USE_SSM": use_ssm,
    }

    return cleaned


class ConfigSettings:
    """
    Immutable container holding cached startup configuration settings.
    Guarantees secrets are not printed, logged, or exposed in repr/str.
    """

    def __init__(self, data: Dict[str, Any]):
        self._data = dict(data)
        self.ADMIN_USERNAME: str = data["ADMIN_USERNAME"]
        self.ADMIN_PASSWORD: str = data["ADMIN_PASSWORD"]
        self.SECRET_KEY: str = data["SECRET_KEY"]
        self.DATABASE_URL: str = data["DATABASE_URL"]
        self.S3_BUCKET: str = data["S3_BUCKET"]
        self.AWS_REGION: str = data["AWS_REGION"]
        self.ALLOWED_ORIGIN: str = data["ALLOWED_ORIGIN"]
        self.PORT: int = data["PORT"]
        self.STORAGE_BACKEND: str = data["STORAGE_BACKEND"]
        self.USE_SSM: bool = data["USE_SSM"]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, item: str) -> Any:
        return self._data[item]

    def __repr__(self) -> str:
        # Secrets are explicitly masked to prevent accidental leakage in logs/debuggers
        return (
            f"<ConfigSettings "
            f"ADMIN_USERNAME={self.ADMIN_USERNAME!r} "
            f"ADMIN_PASSWORD='***' "
            f"SECRET_KEY='***' "
            f"DATABASE_URL='***' "
            f"S3_BUCKET={self.S3_BUCKET!r} "
            f"AWS_REGION={self.AWS_REGION!r} "
            f"ALLOWED_ORIGIN={self.ALLOWED_ORIGIN!r} "
            f"PORT={self.PORT} "
            f"STORAGE_BACKEND={self.STORAGE_BACKEND!r} "
            f"USE_SSM={self.USE_SSM}>"
        )

    def __str__(self) -> str:
        return self.__repr__()


# Cached singleton instance
_cached_config: Optional[ConfigSettings] = None


def load_config(force_reload: bool = False) -> ConfigSettings:
    """
    Load and cache configuration at startup.
    Subsequent calls return the cached in-memory instance unless force_reload=True.
    """
    global _cached_config
    if _cached_config is None or force_reload:
        data = _load_and_validate_config()
        _cached_config = ConfigSettings(data)
    return _cached_config


# Initialize and cache settings at module import time (startup)
config: ConfigSettings = load_config()

# Module-level exports for convenient direct imports
ADMIN_USERNAME: str = config.ADMIN_USERNAME
ADMIN_PASSWORD: str = config.ADMIN_PASSWORD
SECRET_KEY: str = config.SECRET_KEY
DATABASE_URL: str = config.DATABASE_URL
S3_BUCKET: str = config.S3_BUCKET
AWS_REGION: str = config.AWS_REGION
ALLOWED_ORIGIN: str = config.ALLOWED_ORIGIN
PORT: int = config.PORT
STORAGE_BACKEND: str = config.STORAGE_BACKEND
USE_SSM: bool = config.USE_SSM


class Config:
    """Flask configuration object compatible with app.config.from_object(Config)."""

    ADMIN_USERNAME = ADMIN_USERNAME
    ADMIN_PASSWORD = ADMIN_PASSWORD
    SECRET_KEY = SECRET_KEY
    DATABASE_URL = DATABASE_URL
    S3_BUCKET = S3_BUCKET
    AWS_REGION = AWS_REGION
    ALLOWED_ORIGIN = ALLOWED_ORIGIN
    PORT = PORT
    STORAGE_BACKEND = STORAGE_BACKEND
    USE_SSM = USE_SSM
