"""Versioned immutable credential-name floor shared by live and safe domains."""

MANDATORY_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "api-key",
        "authorization",
        "ocp-apim-subscription-key",
        "proxy-authorization",
        "x-amz-security-token",
        "x-access-token",
        "x-api-key",
        "x-auth-token",
        "x-functions-key",
        "x-goog-api-key",
        "x-master-key",
        "x-subscription-key",
    }
)

MANDATORY_SENSITIVE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api-key",
        "api_key",
        "apikey",
        "auth_token",
        "client_assertion",
        "client_secret",
        "code",
        "id_token",
        "key",
        "password",
        "refresh_token",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
    }
)

__all__ = ("MANDATORY_SENSITIVE_HEADER_NAMES", "MANDATORY_SENSITIVE_QUERY_NAMES")
