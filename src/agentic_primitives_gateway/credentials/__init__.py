"""Per-user credential resolution subsystem.

Resolves credentials from OIDC user attributes, caches them, and populates
request-scoped contextvars so providers work unchanged.
"""
