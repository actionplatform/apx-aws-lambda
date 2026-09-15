"""The proxy's decisions with IAM, STS and DynamoDB faked: routing, organization pinning, admin scope, grants by subject prefix."""

import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    import boto3  # noqa: F401

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


class NoSuchEntity(Exception):
    pass


@unittest.skipUnless(HAS_DEPS, "boto3 is not installed")
class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(
            ISSUER_URL="https://platform.test",
            ORGANIZATION="acme",
            TABLE="t",
            BOUNDARY_ARN="arn:aws:iam::1:policy/ActionPlatformBoundary",
            ACCOUNT_ID="1",
            PROXY_VERSION="0.1.0",
            AWS_DEFAULT_REGION="us-east-1",
            AWS_ACCESS_KEY_ID="x",
            AWS_SECRET_ACCESS_KEY="x",
        )
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        cls.app = importlib.import_module("app")

    def setUp(self):
        self.rows: dict[str, dict] = {}
        table = mock.MagicMock()
        table.get_item.side_effect = lambda Key: {"Item": self.rows.get(Key["app"])}
        table.put_item.side_effect = lambda Item: self.rows.__setitem__(
            Item["app"], Item
        )
        table.update_item.side_effect = lambda Key, **kw: self.rows[
            Key["app"]
        ].__setitem__("subjects", kw["ExpressionAttributeValues"][":s"])
        self.sts = mock.MagicMock()
        self.sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIA",
                "SecretAccessKey": "s",
                "SessionToken": "t",
                "Expiration": mock.MagicMock(isoformat=lambda: "2030"),
            }
        }
        self.sts.get_caller_identity.return_value = {
            "Arn": "arn:aws:sts::1:assumed-role/proxy-role/fn"
        }
        self.iam = mock.MagicMock()
        self.iam.exceptions.NoSuchEntityException = NoSuchEntity
        self.patches = [
            mock.patch.object(self.app, "table", table),
            mock.patch.object(self.app, "sts", self.sts),
            mock.patch.object(self.app, "iam", self.iam),
        ]

        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def call(self, method, path, claims=None, body=None):
        event = {
            "requestContext": {"http": {"method": method}, "domainName": "p.test"},
            "rawPath": path,
            "headers": {"authorization": "Bearer x"} if claims is not None else {},
            "body": json.dumps(body) if body else None,
        }

        with mock.patch.object(self.app, "verify", lambda h, u: claims):
            out = self.app.handler(event, None)

        return out["statusCode"], json.loads(out["body"])

    def test_health_needs_no_token(self):
        status, data = self.call("GET", "/health")

        self.assertEqual(
            (status, data["version"], data["organization"]), (200, "0.1.0", "acme")
        )

    def test_admin_calls_need_org_manage(self):
        status, data = self.call(
            "POST",
            "/apps/acme/shop/orders",
            {"sub": "org:acme", "organization": "acme"},
        )

        self.assertEqual((status, data["error"]), (403, "org.manage is required"))

    def test_create_makes_both_roles_and_grants_the_app_itself(self):
        self.iam.get_role.side_effect = self.iam.exceptions.NoSuchEntityException = (
            type("NoSuchEntityException", (Exception,), {})
        )
        self.iam.create_role.return_value = {"Role": {"Arn": "arn"}}
        admin = {"sub": "org:acme", "organization": "acme", "scopes": ["org.manage"]}

        status, data = self.call("POST", "/apps/acme/shop/orders", admin)

        self.assertEqual(status, 200)
        self.assertEqual(data["subjects"], ["org:acme:project:shop:app:orders"])
        created = [c.kwargs["RoleName"] for c in self.iam.create_role.call_args_list]
        self.assertEqual(
            created, ["ap-exec-acme-shop-orders", "ap-deploy-acme-shop-orders"]
        )
        exec_call = self.iam.create_role.call_args_list[0].kwargs
        self.assertEqual(exec_call["PermissionsBoundary"], os.environ["BOUNDARY_ARN"])
        self.assertEqual(exec_call["Path"], "/action-platform/")

    def test_credentials_follow_the_grants_by_prefix(self):
        self.rows["acme/shop/orders"] = {
            "app": "acme/shop/orders",
            "region": "us-east-1",
            "subjects": ["org:acme:project:shop"],
        }
        path = "/apps/acme/shop/orders/credentials"

        ok, data = self.call(
            "POST",
            path,
            {"sub": "org:acme:project:shop:app:orders", "organization": "acme"},
        )
        other, refused = self.call(
            "POST",
            path,
            {"sub": "org:acme:project:billing:app:x", "organization": "acme"},
        )

        self.assertEqual(
            (ok, data["access_key_id"], data["stack_prefix"]),
            (200, "AKIA", "ap-acme-shop-orders"),
        )
        self.assertEqual(
            self.sts.assume_role.call_args.kwargs["RoleArn"],
            "arn:aws:iam::1:role/action-platform/ap-deploy-acme-shop-orders",
        )
        self.assertEqual(other, 403)
        self.assertIn("may not deploy", refused["error"])

    def test_grants_stay_inside_the_organization(self):
        self.rows["acme/shop/orders"] = {"app": "acme/shop/orders", "subjects": []}
        admin = {"sub": "org:acme", "organization": "acme", "scopes": ["org.manage"]}

        status, data = self.call(
            "PUT", "/apps/acme/shop/orders/grants", admin, {"subjects": ["org:evil"]}
        )

        self.assertEqual(status, 400)
        self.assertIn("outside organization", data["error"])

    def test_another_organization_is_refused(self):
        status, data = self.call(
            "POST",
            "/apps/other/shop/orders/credentials",
            {"sub": "org:other", "organization": "acme"},
        )

        self.assertEqual(status, 403)
        self.assertIn("serves organization", data["error"])
