"""Application configuration, read from the process environment only.

There is no `.env` fallback. The deployed services read real environment
variables, `DATABASE_URL` among them supplied by Render's `fromDatabase` wiring
(specs/tech-stack.md section 10), and a local developer exports them or uses the
compose defaults. A file fallback would make it possible to deploy a service
that silently read a stale local value.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# SQLAlchemy selects its DBAPI from the URL. A bare "postgresql://" means
# psycopg2, which this project does not install; "postgres://" is rejected
# outright by SQLAlchemy 2.0. Render's connection string uses one of those two
# spellings, so the driver is normalised on the way in rather than depended on.
_DRIVER = "postgresql+psycopg"
_ACCEPTED_SCHEMES = ("postgresql+psycopg://", "postgresql://", "postgres://")


class ConfigurationError(RuntimeError):
    """The environment does not carry a usable configuration.

    Raised instead of letting a ValidationError escape, so a missing variable
    produces a message naming the variable rather than a schema dump.
    """


class Settings(BaseSettings):
    """Settings for every component: web service, worker and cron invocation."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # No default, deliberately. A missing DATABASE_URL must fail at startup with
    # a named error rather than falling back to localhost and then failing
    # confusingly against a deployed environment.
    database_url: SecretStr

    log_level: str = "INFO"
    environment: Literal["local", "ci", "production"] = "local"

    # How long the worker waits between ticks. In Phase 1 a tick only logs; in
    # Phase 8 it becomes a claim against the `jobs` table, where this doubles as
    # the queue's polling latency (specs/tech-stack.md section 4 accepts a
    # second or two).
    worker_tick_seconds: float = 30.0

    # Where the marketplace connector reads records from
    # (specs/2026-11-08-block-a-marketplace-connector/requirements.md D10).
    #
    # **No default, deliberately**, and for a different reason than
    # DATABASE_URL's. A default here would be a plausible-looking host - a
    # localhost, or this service's own URL - and a deployed connector that fell
    # back to one would reconcile a tenant's books against the wrong
    # marketplace's records and report every difference as a discrepancy. That
    # is a wrong financial answer produced confidently, which specs/mission.md
    # P3 forbids. Absent means the connector refuses to run, and refusing is
    # the only safe failure for this one.
    #
    # Optional rather than required because the web service and the worker both
    # read this Settings object and neither needs it; the connector asserts it
    # is set at the point of use.
    marketplace_base_url: str | None = None

    # PayPal sandbox credentials, for the demo's one live order (R15).
    #
    # **All optional, and the service runs without them.** R6 means the
    # reconciliation never calls PayPal - records are replayed from captured
    # fixtures - so these are needed only for the opening flourish. Absent
    # credentials make `try_live_order` return None and the demo proceeds
    # from the seeded data, which is exactly R15's stated behaviour.
    #
    # Sandbox only. `reckonai.paypal.capture` refuses a non-sandbox base URL
    # rather than trusting this default.
    paypal_client_id: SecretStr | None = None
    paypal_client_secret: SecretStr | None = None
    paypal_bn_code: str | None = None
    paypal_api_base: str = "https://api-m.sandbox.paypal.com"

    # Injected by Render into web services; absent locally, in CI, and in the
    # worker, which has no public URL. Logged on startup so a deployed service
    # announces its own address rather than it having to be found in a
    # dashboard - which is what makes the deployed checks in this phase's
    # validation.md section 4 readable straight from the log stream.
    render_external_url: str | None = None

    @field_validator("database_url")
    @classmethod
    def _normalise_driver(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value().strip()

        if raw.startswith("postgresql+psycopg://"):
            return SecretStr(raw)

        for scheme in ("postgres://", "postgresql://"):
            if raw.startswith(scheme):
                return SecretStr(f"{_DRIVER}://{raw[len(scheme):]}")

        raise ValueError(
            "DATABASE_URL must use one of "
            f"{', '.join(_ACCEPTED_SCHEMES)} - got an unrecognised scheme"
        )

    @property
    def sqlalchemy_url(self) -> str:
        """The connection URL, driver normalised, for handing to SQLAlchemy."""
        return self.database_url.get_secret_value()

    def __repr__(self) -> str:
        # The URL carries a password. SecretStr already redacts it, and this
        # keeps it redacted even if the field type is ever changed.
        return (
            f"Settings(environment={self.environment!r}, "
            f"log_level={self.log_level!r}, database_url=<redacted>)"
        )

    __str__ = __repr__


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings once per process.

    Cached because every component reads the same environment and because a
    repeated read would be a repeated chance to log a credential.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = sorted(
            str(error["loc"][0]).upper()
            for error in exc.errors()
            if error["type"] == "missing"
        )
        if missing:
            raise ConfigurationError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            ) from exc
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc
