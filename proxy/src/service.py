"""What the proxy does for an app: register it (roles and row), show it, grant it, hand out its credentials, forget it. One method per route, no HTTP in sight."""

from __future__ import annotations

from apps import App
from auth import Auth
from credentials import Credentials
from errors import Refused
from registry import Registry
from roles import Roles
from settings import POLICY_VERSION, Settings


class ProxyService:
    def __init__(
        self,
        settings: Settings,
        roles: Roles,
        registry: Registry,
        credentials: Credentials,
    ) -> None:
        self.settings = settings
        self.roles = roles
        self.registry = registry
        self.credentials = credentials

    def app(self, org: str, project: str, name: str) -> App:
        return App(org, project, name, self.settings)

    def health(self) -> dict:
        return {
            "version": self.settings.version,
            "issuer": self.settings.issuer,
            "organization": self.settings.organization,
            "boundary": self.settings.boundary_arn,
            "account": self.settings.account_id,
        }

    def create(self, claims: dict, app: App, region: str | None) -> dict:
        Auth.admin(claims)
        chosen = region or self.settings.default_region
        self.roles.create(app, chosen)
        row = self.registry.register(app, chosen, Auth.actor(claims))

        return app.view(row)

    def show(self, claims: dict, app: App) -> dict:
        Auth.admin(claims)

        return app.view(self.registry.require(app))

    def delete(self, claims: dict, app: App) -> dict:
        Auth.admin(claims)
        self.roles.delete(app)
        self.registry.forget(app)

        return {"app": app.key, "deleted": True}

    def grant(self, claims: dict, app: App, subjects: object) -> dict:
        Auth.admin(claims)

        if not isinstance(subjects, list) or not all(
            isinstance(s, str) for s in subjects
        ):
            raise ValueError("subjects must be a list of subject prefixes")

        for subject in subjects:
            if not subject.startswith(f"org:{self.settings.organization}"):
                raise ValueError(
                    f"subject {subject!r} is outside organization {self.settings.organization!r}"
                )

        self.registry.require(app)
        self.registry.set_subjects(app, subjects)

        return app.view(self.registry.require(app))

    def credentials_for(self, claims: dict, app: App, duration: object) -> dict:
        row = self.registry.require(app)
        subject = claims["sub"]

        if not Registry.granted(row, subject):
            raise Refused(403, f"{subject} may not deploy {app.key}")

        if int(row.get("policy") or 0) < POLICY_VERSION:
            self.roles.refresh_policy(
                app, row.get("region") or self.settings.default_region
            )
            self.registry.mark_policy(row)

        creds = self.credentials.assume(
            app.role_arn("deploy"),
            Credentials.session_name(claims.get("actor")),
            Credentials.duration(duration),
        )

        return {
            "app": app.key,
            "region": row.get("region"),
            "stack_prefix": app.prefix,
            "execution_role": app.role_arn("exec"),
            "deploy_role": app.role_arn("deploy"),
            "access_key_id": creds["AccessKeyId"],
            "secret_access_key": creds["SecretAccessKey"],
            "session_token": creds["SessionToken"],
            "expiration": creds["Expiration"].isoformat(),
        }
