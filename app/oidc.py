from authlib.integrations.starlette_client import OAuth
from . import control_models


def build_oidc_client(settings: control_models.AppSettings):
    """Build a fresh Authlib OAuth client from the current DB-backed settings.
    Built per-request rather than once at startup, since config can change
    from the Settings page without a restart."""
    if not (settings and settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret):
        return None

    issuer = settings.oidc_issuer.rstrip("/")
    oauth = OAuth()
    oauth.register(
        name="sso",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )
    return oauth.sso
