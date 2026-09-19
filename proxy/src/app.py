"""The deploy proxy's HTTP edge: a Lambda function URL event in, JSON out. Routes name a method of ProxyService; everything else — roles, rows, tokens, credentials — lives in its own module.

Wiring happens here and only here: the AWS clients, the key cache and the settings are module state (a Lambda container keeps them warm), and each request builds the service on top of them."""

from __future__ import annotations

import json
import re
import time  # noqa: F401 — the retry's sleep is patched here by the tests
from typing import Any, Callable

import boto3
from apps import App as _App
from auth import Auth
from botocore.exceptions import ClientError
from credentials import Credentials
from errors import Refused
from oidc import Keys, TokenError
from registry import Registry
from roles import Roles
from service import ProxyService
from settings import POLICY_VERSION, Settings

settings = Settings.from_env()
keys = Keys(settings.jwks_url)
iam = boto3.client("iam")
sts = boto3.client("sts")
table = boto3.resource("dynamodb").Table(settings.table) if settings.table else None

APP = re.compile(
    r"^/apps/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<app>[^/]+)(?P<rest>/grants|/credentials)?$"
)

__all__ = ["App", "ClientError", "POLICY_VERSION", "handler", "verify"]


def App(org: str, project: str, name: str) -> _App:
    return _App(org, project, name, settings)


def service() -> ProxyService:
    return ProxyService(
        settings, Roles(iam, sts, settings), Registry(table), Credentials(sts)
    )


def verify(headers: dict, own_url: str) -> dict:
    return Auth(keys, settings).claims(headers, own_url)


def handler(event: dict, context: Any) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/").rstrip("/") or "/"
    own_url = "https://" + event.get("requestContext", {}).get("domainName", "")

    try:
        if method == "GET" and path == "/health":
            return reply(200, service().health())

        route, target = match(method, path)
        body = json.loads(event.get("body") or "{}") if event.get("body") else {}
        claims = verify(event.get("headers") or {}, own_url)

        return reply(200, route(service(), claims, target, body))
    except Refused as e:
        return reply(e.status, {"error": str(e)})
    except TokenError as e:
        return reply(401, {"error": f"token: {e}"})
    except ValueError as e:
        return reply(400, {"error": str(e)})
    except ClientError as e:
        return reply(500, {"error": f"aws: {e}"})


Route = Callable[[ProxyService, dict, _App, dict], dict]

ROUTES: dict[tuple[str, str], Route] = {
    ("POST", ""): lambda s, c, a, b: s.create(c, a, b.get("region")),
    ("GET", ""): lambda s, c, a, b: s.show(c, a),
    ("DELETE", ""): lambda s, c, a, b: s.delete(c, a),
    ("PUT", "/grants"): lambda s, c, a, b: s.grant(c, a, b.get("subjects")),
    ("POST", "/credentials"): lambda s, c, a, b: s.credentials_for(
        c, a, b.get("duration")
    ),
}


def match(method: str, path: str) -> tuple[Route, _App]:
    found = APP.match(path)
    route = (
        ROUTES.get((method, (found.group("rest") or "") if found else ""))
        if found
        else None
    )

    if found is None or route is None:
        raise Refused(404, f"no route {method} {path}")

    return route, App(found.group("org"), found.group("project"), found.group("app"))


def reply(status: int, data: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(data),
    }
