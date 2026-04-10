from enum import StrEnum


class Primitive(StrEnum):
    MEMORY = "memory"
    OBSERVABILITY = "observability"
    LLM = "llm"
    TOOLS = "tools"
    IDENTITY = "identity"
    CODE_INTERPRETER = "code_interpreter"
    BROWSER = "browser"
    POLICY = "policy"
    EVALUATIONS = "evaluations"
    TASKS = "tasks"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"


class TokenType(StrEnum):
    BEARER = "Bearer"


class AuthFlow(StrEnum):
    M2M = "M2M"
    USER_FEDERATION = "USER_FEDERATION"


class CredentialProviderType(StrEnum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"


class CodeLanguage(StrEnum):
    PYTHON = "python"


class ServerCredentialMode(StrEnum):
    NEVER = "never"
    FALLBACK = "fallback"
    ALWAYS = "always"


class HealthStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    DEGRADED = "degraded"
    ACCEPTED = "accepted"
