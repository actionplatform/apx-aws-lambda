"""The deploy proxy's client: a platform token for the proxy's url, exchanged for the app's deploy-role credentials — the account owner decided in the proxy who may deploy what."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from action_platform.core.context import Context
from action_platform.core.exception import ActionPlatformError, DeployError
from action_platform.remote.client import Remote

MIN_PROXY = "0.1.0"


class ProxyClient:
    def __init__(self, url: str, app: str, timeout: float = 20.0) -> None:
        self.url = url.rstrip("/")
        self.app = app.strip("/")
        self.timeout = timeout

        if self.app.count("/") != 2:
            raise DeployError(
                f"[deploy] app must be <org>/<project>/<app>, got {app!r}"
            )

    def token(self, ctx: Context | None = None) -> str:
        token = ctx.identity_token(self.url) if ctx is not None else None

        if token is not None:
            return token

        try:
            return Remote.from_credentials().identity_token(self.url)["token"]
        except ActionPlatformError as e:
            raise DeployError(
                f"proxy_url is set but nothing can sign an identity token here: {e}. "
                "Deploy from the platform, or log in with `action-platform login`."
            ) from e

    def health(self) -> dict:
        return self._call("GET", "/health")

    def credentials(self, ctx: Context, duration: int = 3600) -> dict:
        health = self.health()

        if tuple_of(health.get("version", "0")) < tuple_of(MIN_PROXY):
            raise DeployError(
                f"proxy {self.url} is {health.get('version')}; this plugin needs {MIN_PROXY} or newer — update the proxy stack"
            )

        return self._call(
            "POST",
            f"/apps/{self.app}/credentials",
            {"duration": duration},
            token=self.token(ctx),
        )

    def create(self, region: str | None = None) -> dict:
        """Admin: both roles for the app, granted to the app itself — the caller's token must carry `org.manage`."""
        return self._call(
            "POST",
            f"/apps/{self.app}",
            {"region": region} if region else {},
            token=self.token(),
        )

    def show(self) -> dict:
        return self._call("GET", f"/apps/{self.app}", token=self.token())

    def delete(self) -> dict:
        return self._call("DELETE", f"/apps/{self.app}", token=self.token())

    def grant(self, subjects: list[str]) -> dict:
        return self._call(
            "PUT",
            f"/apps/{self.app}/grants",
            {"subjects": subjects},
            token=self.token(),
        )

    def env(self, ctx: Context) -> dict[str, str]:
        data = self.credentials(ctx)

        return {
            "AWS_ACCESS_KEY_ID": data["access_key_id"],
            "AWS_SECRET_ACCESS_KEY": data["secret_access_key"],
            "AWS_SESSION_TOKEN": data["session_token"],
            "AP_STACK_PREFIX": data.get("stack_prefix", ""),
            "AP_EXECUTION_ROLE": data.get("execution_role", ""),
        }

    def _call(
        self, method: str, path: str, body: dict | None = None, token: str | None = None
    ) -> dict:
        headers = {"accept": "application/json"}

        if token:
            headers["authorization"] = f"Bearer {token}"

        data = None

        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode()

        request = urllib.request.Request(
            self.url + path, data=data, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")

            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass

            raise DeployError(f"proxy {method} {path}: {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise DeployError(f"proxy {self.url} unreachable: {e.reason}") from e


def tuple_of(version: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))
