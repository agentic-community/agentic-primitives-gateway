from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


def _expand_vars(text: str) -> str:
    """Expand shell-style variables in text.

    Supports:
        ${VAR}            — replaced with env value, or empty string
        ${VAR:=default}   — replaced with env value, or 'default' if unset
        ${VAR:-default}   — same as :=
        $VAR              — simple form
    """

    def _replace(match: re.Match) -> str:
        var = match.group("var")
        default = match.group("default")
        value = os.environ.get(var)
        if value is not None:
            return value
        if default is not None:
            return str(default)
        return ""

    # Handle ${VAR:=default} and ${VAR:-default} first
    text = str(
        re.sub(
            r"\$\{(?P<var>[A-Za-z_][A-Za-z0-9_]*)[:][=\-](?P<default>[^}]*)\}",
            _replace,
            text,
        )
    )
    # Then handle plain ${VAR} and $VAR
    text = os.path.expandvars(text)
    return text


from pydantic import BaseModel, Field, model_validator  # noqa: E402
from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from agentic_primitives_gateway.models.enums import LogLevel, Primitive, ServerCredentialMode  # noqa: E402


class ProviderConfig(BaseModel):
    backend: str
    config: dict = Field(default_factory=dict)


class PrimitiveProvidersConfig(BaseModel):
    """Configuration for a single primitive with named backends.

    Example::

        memory:
          default: "mem0"
          backends:
            mem0:
              backend: "...Mem0MemoryProvider"
              config: { ... }
            agentcore:
              backend: "...AgentCoreMemoryProvider"
              config: { ... }
    """

    default: str
    backends: dict[str, ProviderConfig]

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Allow shorthand: {backend: "...", config: {}} → full multi-provider format."""
        if isinstance(data, dict) and "backend" in data and "backends" not in data:
            return {
                "default": "default",
                "backends": {
                    "default": {
                        "backend": data["backend"],
                        "config": data.get("config", {}),
                    }
                },
            }
        return data


_DEFAULTS: dict[str, dict[str, Any]] = {
    Primitive.MEMORY: {
        "default": "in_memory",
        "backends": {
            "in_memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
            }
        },
    },
    Primitive.OBSERVABILITY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
            }
        },
    },
    Primitive.LLM: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
            }
        },
    },
    Primitive.TOOLS: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
            }
        },
    },
    Primitive.IDENTITY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
            }
        },
    },
    Primitive.CODE_INTERPRETER: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
            }
        },
    },
    Primitive.BROWSER: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
            }
        },
    },
    Primitive.POLICY: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider",
            }
        },
    },
    Primitive.EVALUATIONS: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider",
            }
        },
    },
    Primitive.TASKS: {
        "default": "noop",
        "backends": {
            "noop": {
                "backend": "agentic_primitives_gateway.primitives.tasks.noop.NoopTasksProvider",
            }
        },
    },
}


class ProvidersConfig(BaseModel):
    memory: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.MEMORY])
    observability: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.OBSERVABILITY])
    llm: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.LLM])
    tools: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.TOOLS])
    identity: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.IDENTITY])
    code_interpreter: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.CODE_INTERPRETER])
    browser: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.BROWSER])
    policy: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.POLICY])
    evaluations: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.EVALUATIONS])
    tasks: PrimitiveProvidersConfig = PrimitiveProvidersConfig(**_DEFAULTS[Primitive.TASKS])


class SeedPolicyConfig(BaseModel):
    """A Cedar policy to seed into the policy provider at startup."""

    description: str = ""
    policy_body: str


class AuthConfig(BaseModel):
    """Configuration for the authentication layer.

    ``backend`` is a short alias (``noop``, ``api_key``, ``jwt``) or a
    fully-qualified dotted class path.
    """

    backend: str = "noop"
    api_keys: list[dict[str, Any]] = Field(default_factory=list)
    jwt: dict[str, Any] = Field(default_factory=dict)


# Well-known auth backend aliases → dotted class paths
AUTH_BACKEND_ALIASES: dict[str, str] = {
    "noop": "agentic_primitives_gateway.auth.noop.NoopAuthBackend",
    "api_key": "agentic_primitives_gateway.auth.api_key.ApiKeyAuthBackend",
    "jwt": "agentic_primitives_gateway.auth.jwt.JwtAuthBackend",
}


class EnforcementConfig(BaseModel):
    """Configuration for the policy enforcement layer."""

    backend: str = "agentic_primitives_gateway.enforcement.noop.NoopPolicyEnforcer"
    config: dict[str, Any] = Field(default_factory=dict)
    seed_policies: list[SeedPolicyConfig] = Field(default_factory=list)


class StoreConfig(BaseModel):
    """Pluggable store backend configuration.

    ``backend`` is a dotted class path or a short alias (``file``, ``redis``).
    ``config`` is passed as kwargs to the store constructor.
    """

    backend: str = "file"
    config: dict[str, Any] = Field(default_factory=dict)


class AgentsConfig(BaseModel):
    """Configuration for the agents subsystem."""

    store: StoreConfig = StoreConfig()
    default_model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    max_turns: int = 20
    specs: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TeamsConfig(BaseModel):
    """Configuration for the teams subsystem."""

    store: StoreConfig = StoreConfig()
    specs: dict[str, dict[str, Any]] = Field(default_factory=dict)


# Well-known store backend aliases → dotted class paths
AGENT_STORE_ALIASES: dict[str, str] = {
    "file": "agentic_primitives_gateway.agents.store.FileAgentStore",
    "redis": "agentic_primitives_gateway.agents.redis_store.RedisAgentStore",
}

TEAM_STORE_ALIASES: dict[str, str] = {
    "file": "agentic_primitives_gateway.agents.team_store.FileTeamStore",
    "redis": "agentic_primitives_gateway.agents.redis_store.RedisTeamStore",
}


class AwsFederationConfig(BaseModel):
    """AWS OIDC federation configuration (Phase 4)."""

    enabled: bool = False
    sts_region: str = "us-east-1"
    session_duration: int = 3600
    role_arn_attribute: str = "apg.aws_role_arn"


class OidcResolverConfig(BaseModel):
    """OIDC credential resolver configuration.

    No explicit attribute mapping needed — the resolver auto-discovers
    all ``apg.*`` claims from userinfo and maps them by convention:
    ``apg.{service}.{key}`` → ``service_credentials[service][key]``.
    """

    aws: AwsFederationConfig = AwsFederationConfig()


class CredentialWriterConfig(BaseModel):
    """Credential writer backend configuration."""

    backend: str = "noop"
    config: dict[str, Any] = Field(default_factory=dict)


class CredentialCacheConfig(BaseModel):
    """Credential cache configuration."""

    ttl_seconds: int = 300
    max_entries: int = 10000


class CredentialsConfig(BaseModel):
    """Configuration for per-user credential resolution."""

    resolver: str = "noop"
    oidc: OidcResolverConfig = OidcResolverConfig()
    writer: CredentialWriterConfig = CredentialWriterConfig()
    cache: CredentialCacheConfig = CredentialCacheConfig()


# Well-known credential resolver aliases → dotted class paths
CREDENTIAL_RESOLVER_ALIASES: dict[str, str] = {
    "noop": "agentic_primitives_gateway.credentials.noop.NoopCredentialResolver",
    "oidc": "agentic_primitives_gateway.credentials.oidc.OidcCredentialResolver",
}

# Well-known credential writer aliases → dotted class paths
CREDENTIAL_WRITER_ALIASES: dict[str, str] = {
    "noop": "agentic_primitives_gateway.credentials.writer.noop.NoopCredentialWriter",
    "keycloak": "agentic_primitives_gateway.credentials.writer.keycloak.KeycloakCredentialWriter",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_PRIMITIVES_GATEWAY_",
        env_nested_delimiter="__",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = LogLevel.INFO
    config_file: str | None = None
    allow_server_credentials: ServerCredentialMode = ServerCredentialMode.NEVER
    cors_origins: list[str] = ["*"]
    providers: ProvidersConfig = ProvidersConfig()
    auth: AuthConfig = AuthConfig()
    enforcement: EnforcementConfig = EnforcementConfig()
    credentials: CredentialsConfig = CredentialsConfig()
    agents: AgentsConfig = AgentsConfig()
    teams: TeamsConfig = TeamsConfig()

    @staticmethod
    def config_file_path() -> str | None:
        """Return the config file path from the environment, or None."""
        return os.environ.get("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE")

    @classmethod
    def load(cls) -> Settings:
        """Load settings, merging config file values with env overrides."""
        config_path = os.environ.get("AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE")
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                raw = f.read()
            expanded = _expand_vars(raw)
            file_config = yaml.safe_load(expanded) or {}
            return cls(**file_config)
        return cls()


settings = Settings.load()
