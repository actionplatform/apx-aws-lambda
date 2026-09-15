"""The deploy proxy: a platform token in, a short-lived credential of one app's deploy role out — when the app's grants say so. Admin calls (create an app's roles, set grants) need `org.manage` in the token's scopes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Callable

import boto3
from oidc import Keys, TokenError
from oidc import verify as verify_token

ISSUER = os.environ.get("ISSUER_URL", "").rstrip("/")
ORGANIZATION = os.environ.get("ORGANIZATION", "")
TABLE = os.environ.get("TABLE", "")
SAM_BUCKET = "aws-sam-cli-managed-default"
BOUNDARY_ARN = os.environ.get("BOUNDARY_ARN", "")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "")
VERSION = os.environ.get("PROXY_VERSION", "0.0.0")
PATH = "/action-platform/"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MAX_DURATION = 3600
MIN_DURATION = 900

keys = Keys(f"{ISSUER}/.well-known/jwks.json")
iam = boto3.client("iam")
sts = boto3.client("sts")
table = boto3.resource("dynamodb").Table(TABLE) if TABLE else None


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, org: str, project: str, name: str) -> None:
        for part in (org, project, name):
            if not SLUG.match(part):
                raise Refused(400, f"bad slug {part!r}")

        if org != ORGANIZATION:
            raise Refused(403, f"this proxy serves organization {ORGANIZATION!r}")

        self.org, self.project, self.name = org, project, name

    @property
    def key(self) -> str:
        return f"{self.org}/{self.project}/{self.name}"

    @property
    def subject(self) -> str:
        return f"org:{self.org}:project:{self.project}:app:{self.name}"

    @property
    def prefix(self) -> str:
        return f"ap-{self.org}-{self.project}-{self.name}"

    def role_name(self, kind: str) -> str:
        """`ap-<kind>-<org>-<project>-<app>`, shortened with a hash when IAM's 64 characters run out."""
        name = f"ap-{kind}-{self.org}-{self.project}-{self.name}"

        if len(name) <= 64:
            return name

        digest = hashlib.sha256(name.encode()).hexdigest()[:8]

        return f"{name[:55]}-{digest}"

    def role_arn(self, kind: str) -> str:
        return f"arn:aws:iam::{ACCOUNT_ID}:role{PATH}{self.role_name(kind)}"

    def deploy_policy(self, region: str) -> dict:
        stack = f"arn:aws:cloudformation:{region}:{ACCOUNT_ID}:stack/{self.prefix}*"

        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["cloudformation:*"],
                    "Resource": [stack],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "cloudformation:ListStacks",
                        "cloudformation:ValidateTemplate",
                        "cloudformation:GetTemplateSummary",
                        "cloudformation:CreateChangeSet",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["lambda:*"],
                    "Resource": [
                        f"arn:aws:lambda:{region}:{ACCOUNT_ID}:function:{self.prefix}*",
                        f"arn:aws:lambda:{region}:{ACCOUNT_ID}:layer:{self.prefix}*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["apigateway:*"],
                    "Resource": [f"arn:aws:apigateway:{region}::/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["logs:*"],
                    "Resource": [
                        f"arn:aws:logs:{region}:{ACCOUNT_ID}:log-group:/aws/lambda/{self.prefix}*"
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["iam:PassRole", "iam:GetRole"],
                    "Resource": [self.role_arn("exec")],
                },
                {
                    "Effect": "Allow",
                    "Action": ["cloudformation:*"],
                    "Resource": [
                        f"arn:aws:cloudformation:{region}:{ACCOUNT_ID}:stack/{SAM_BUCKET}*"
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:*"],
                    "Resource": [
                        f"arn:aws:s3:::{SAM_BUCKET}-*",
                        f"arn:aws:s3:::{SAM_BUCKET}-*/*",
                    ],
                },
            ],
        }


def handler(event: dict, context: Any) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/").rstrip("/") or "/"
    own_url = "https://" + event.get("requestContext", {}).get("domainName", "")

    try:
        route, params = match(method, path)
        body = json.loads(event.get("body") or "{}") if event.get("body") else {}

        if route is health:
            return reply(200, health())

        claims = verify(event.get("headers") or {}, own_url)

        return reply(200, route(claims, body, **params))
    except Refused as e:
        return reply(e.status, {"error": str(e)})
    except TokenError as e:
        return reply(401, {"error": f"token: {e}"})
    except ValueError as e:
        return reply(400, {"error": str(e)})


def reply(status: int, data: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(data),
    }


APP = r"/apps/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<app>[^/]+)"


def match(method: str, path: str) -> tuple[Callable[..., dict], dict]:
    routes: list[tuple[str, str, Callable[..., dict]]] = [
        ("GET", r"/health", health),
        ("POST", APP, create_app),
        ("GET", APP, show_app),
        ("DELETE", APP, delete_app),
        ("PUT", APP + r"/grants", set_grants),
        ("POST", APP + r"/credentials", credentials),
    ]

    for verb, pattern, fn in routes:
        found = re.fullmatch(pattern, path)

        if found and verb == method:
            return fn, found.groupdict()

    raise Refused(404, f"no route {method} {path}")


def verify(headers: dict, own_url: str) -> dict:
    auth = headers.get("authorization") or headers.get("Authorization") or ""

    if not auth.lower().startswith("bearer "):
        raise Refused(401, "bearer token required")

    token = auth[7:].strip()
    claims = verify_token(token, keys, ISSUER, [own_url, own_url + "/"])

    if claims.get("organization") != ORGANIZATION:
        raise Refused(403, f"token is for organization {claims.get('organization')!r}")

    return claims


def admin(claims: dict) -> None:
    if "org.manage" not in (claims.get("scopes") or []):
        raise Refused(403, "org.manage is required")


def health() -> dict:
    return {
        "version": VERSION,
        "issuer": ISSUER,
        "organization": ORGANIZATION,
        "boundary": BOUNDARY_ARN,
        "account": ACCOUNT_ID,
    }


def create_app(claims: dict, body: dict, org: str, project: str, app: str) -> dict:
    admin(claims)
    target = App(org, project, app)
    region = body.get("region") or os.environ.get("AWS_REGION", "us-east-1")
    function_role = sts.get_caller_identity()["Arn"]
    proxy_role = re.sub(
        r"^arn:aws:sts::(\d+):assumed-role/([^/]+)/.*$",
        r"arn:aws:iam::\1:role/\2",
        function_role,
    )

    ensure_role(
        target.role_name("exec"),
        trust={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        },
        boundary=BOUNDARY_ARN,
        managed=["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
        tags=target,
    )
    ensure_role(
        target.role_name("deploy"),
        trust={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": proxy_role},
                    "Action": "sts:AssumeRole",
                }
            ],
        },
        inline={"deploy": target.deploy_policy(region)},
        tags=target,
    )
    row = table.get_item(Key={"app": target.key}).get("Item") or {}
    table.put_item(
        Item={
            "app": target.key,
            "region": region,
            "subjects": row.get("subjects") or [target.subject],
            "created_at": row.get("created_at") or int(time.time()),
            "created_by": row.get("created_by") or claims.get("actor") or claims["sub"],
        }
    )

    return show_app(claims, {}, org, project, app)


def ensure_role(
    name: str,
    trust: dict,
    tags: App,
    boundary: str | None = None,
    managed: list[str] | None = None,
    inline: dict[str, dict] | None = None,
) -> str:
    role_tags = [
        {"Key": "action-platform:app", "Value": tags.key},
        {"Key": "action-platform:prefix", "Value": tags.prefix},
        {"Key": "action-platform:managed", "Value": "true"},
    ]

    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        iam.tag_role(RoleName=name, Tags=role_tags)
    except iam.exceptions.NoSuchEntityException:
        kwargs: dict[str, Any] = {
            "RoleName": name,
            "Path": PATH,
            "AssumeRolePolicyDocument": json.dumps(trust),
            "Tags": role_tags,
        }

        if boundary:
            kwargs["PermissionsBoundary"] = boundary

        arn = iam.create_role(**kwargs)["Role"]["Arn"]

    for policy in managed or []:
        iam.attach_role_policy(RoleName=name, PolicyArn=policy)

    for policy_name, document in (inline or {}).items():
        iam.put_role_policy(
            RoleName=name, PolicyName=policy_name, PolicyDocument=json.dumps(document)
        )

    return arn


def show_app(claims: dict, body: dict, org: str, project: str, app: str) -> dict:
    admin(claims)
    target = App(org, project, app)
    row = table.get_item(Key={"app": target.key}).get("Item")

    if row is None:
        raise Refused(404, f"no app {target.key}; create it first")

    return {
        "app": target.key,
        "subject": target.subject,
        "region": row.get("region"),
        "stack_prefix": target.prefix,
        "deploy_role": target.role_arn("deploy"),
        "execution_role": target.role_arn("exec"),
        "subjects": list(row.get("subjects") or []),
    }


def delete_app(claims: dict, body: dict, org: str, project: str, app: str) -> dict:
    admin(claims)
    target = App(org, project, app)

    for kind in ("deploy", "exec"):
        drop_role(target.role_name(kind))

    table.delete_item(Key={"app": target.key})

    return {"app": target.key, "deleted": True}


def drop_role(name: str) -> None:
    try:
        attached = iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]
    except iam.exceptions.NoSuchEntityException:
        return

    for policy in attached:
        iam.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])

    for policy_name in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=name, PolicyName=policy_name)

    iam.delete_role(RoleName=name)


def set_grants(claims: dict, body: dict, org: str, project: str, app: str) -> dict:
    admin(claims)
    target = App(org, project, app)
    subjects = body.get("subjects")

    if not isinstance(subjects, list) or not all(isinstance(s, str) for s in subjects):
        raise ValueError("subjects must be a list of subject prefixes")

    for subject in subjects:
        if not subject.startswith(f"org:{ORGANIZATION}"):
            raise ValueError(
                f"subject {subject!r} is outside organization {ORGANIZATION!r}"
            )

    if table.get_item(Key={"app": target.key}).get("Item") is None:
        raise Refused(404, f"no app {target.key}; create it first")

    table.update_item(
        Key={"app": target.key},
        UpdateExpression="SET subjects = :s",
        ExpressionAttributeValues={":s": sorted(set(subjects))},
    )

    return show_app(claims, {}, org, project, app)


def credentials(claims: dict, body: dict, org: str, project: str, app: str) -> dict:
    target = App(org, project, app)
    row = table.get_item(Key={"app": target.key}).get("Item")

    if row is None:
        raise Refused(404, f"no app {target.key}; create it first")

    sub = claims["sub"]
    granted = any(
        sub == allowed or sub.startswith(allowed + ":")
        for allowed in row.get("subjects") or []
    )

    if not granted:
        raise Refused(403, f"{sub} may not deploy {target.key}")

    duration = int(body.get("duration") or MIN_DURATION)
    duration = max(MIN_DURATION, min(MAX_DURATION, duration))
    session = re.sub(r"[^\w+=,.@-]", "-", claims.get("actor") or "platform")[:64]
    creds = sts.assume_role(
        RoleArn=target.role_arn("deploy"),
        RoleSessionName=session,
        DurationSeconds=duration,
    )["Credentials"]

    return {
        "app": target.key,
        "region": row.get("region"),
        "stack_prefix": target.prefix,
        "execution_role": target.role_arn("exec"),
        "access_key_id": creds["AccessKeyId"],
        "secret_access_key": creds["SecretAccessKey"],
        "session_token": creds["SessionToken"],
        "expiration": creds["Expiration"].isoformat(),
    }
