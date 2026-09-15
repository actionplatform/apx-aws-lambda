"""The proxy client: token for the proxy's url, credentials in, environment out; the target refuses a stack outside the granted prefix."""

import io
import json
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from action_platform.core.context import Context
from action_platform.core.exception import DeployError

from apx_aws_lambda import shell
from apx_aws_lambda.lambda_ import LambdaTarget
from apx_aws_lambda.proxy import ProxyClient

SAMCONFIG = """version = 0.1
[default.deploy.parameters]
stack_name = "{stack}"
region = "us-east-1"
"""

GRANTED = {
    "app": "acme/shop/orders",
    "region": "us-east-1",
    "stack_prefix": "ap-acme-shop-orders",
    "execution_role": "arn:aws:iam::1:role/action-platform/ap-exec-acme-shop-orders",
    "access_key_id": "AKIA",
    "secret_access_key": "s",
    "session_token": "t",
    "expiration": "2030-01-01T00:00:00+00:00",
}


class FakeProxy:
    def __init__(self, answers: dict, version="0.1.0"):
        self.answers = {"GET /health": {"version": version}, **answers}
        self.requests: list[tuple[str, str, dict | None, str | None]] = []

    def __call__(self, request, timeout=None):
        path = request.full_url.split("proxy.test", 1)[1]
        body = json.loads(request.data) if request.data else None
        self.requests.append(
            (request.get_method(), path, body, request.get_header("Authorization"))
        )
        key = f"{request.get_method()} {path}"

        if key not in self.answers:
            raise urllib.error.HTTPError(
                request.full_url, 403, "no", {}, io.BytesIO(b'{"error":"nope"}')
            )

        return mock.MagicMock(
            __enter__=lambda s: s,
            __exit__=lambda s, *a: None,
            read=lambda: json.dumps(self.answers[key]).encode(),
        )


class ProxyClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def ctx(self, token="jwt"):
        return Context(
            repo_root=self.root,
            branch="develop",
            stage="dev",
            identity=(lambda audience: f"{token}:{audience}") if token else None,
        )

    def test_env_carries_credentials_from_the_grant(self):
        fake = FakeProxy({"POST /apps/acme/shop/orders/credentials": GRANTED})

        with mock.patch("urllib.request.urlopen", fake):
            env = ProxyClient("https://proxy.test/", "acme/shop/orders").env(self.ctx())

        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIA")
        self.assertEqual(env["AP_STACK_PREFIX"], "ap-acme-shop-orders")
        self.assertEqual(
            fake.requests[-1],
            (
                "POST",
                "/apps/acme/shop/orders/credentials",
                {"duration": 3600},
                "Bearer jwt:https://proxy.test",
            ),
        )

    def test_a_refusal_is_a_readable_error(self):
        fake = FakeProxy({})

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(DeployError) as caught:
                ProxyClient("https://proxy.test", "acme/shop/orders").env(self.ctx())

        self.assertIn("403 nope", str(caught.exception))

    def test_an_unknown_app_is_registered_on_the_way_when_the_token_may(self):
        fake = FakeProxy({"POST /apps/acme/shop/orders": {"created": True}})
        credentials = "POST /apps/acme/shop/orders/credentials"

        def answer(request, timeout=None):
            if (
                f"{request.get_method()} {request.full_url.split('proxy.test', 1)[1]}"
                == credentials
                and credentials not in fake.answers
            ):
                fake.answers[credentials] = GRANTED
                fake.requests.append(
                    (
                        request.get_method(),
                        "/apps/acme/shop/orders/credentials",
                        None,
                        None,
                    )
                )
                raise urllib.error.HTTPError(
                    request.full_url,
                    404,
                    "no",
                    {},
                    io.BytesIO(b'{"error":"no app acme/shop/orders; create it first"}'),
                )

            return fake(request, timeout)

        with mock.patch("urllib.request.urlopen", answer):
            env = ProxyClient("https://proxy.test", "acme/shop/orders").env(
                self.ctx(), "eu-west-1"
            )

        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIA")
        self.assertEqual(
            [(m, p, b) for m, p, b, _ in fake.requests[1:]],
            [
                ("POST", "/apps/acme/shop/orders/credentials", None),
                ("POST", "/apps/acme/shop/orders", {"region": "eu-west-1"}),
                ("POST", "/apps/acme/shop/orders/credentials", {"duration": 3600}),
            ],
        )

    def test_an_unknown_app_the_token_may_not_register_is_a_readable_error(self):
        fake = FakeProxy({})

        def answer(request, timeout=None):
            path = request.full_url.split("proxy.test", 1)[1]

            if path.endswith("/credentials"):
                raise urllib.error.HTTPError(
                    request.full_url,
                    404,
                    "no",
                    {},
                    io.BytesIO(b'{"error":"no app; create it first"}'),
                )

            if path == "/apps/acme/shop/orders":
                raise urllib.error.HTTPError(
                    request.full_url,
                    403,
                    "no",
                    {},
                    io.BytesIO(b'{"error":"org.manage is required"}'),
                )

            return fake(request, timeout)

        with mock.patch("urllib.request.urlopen", answer):
            with self.assertRaises(DeployError) as caught:
                ProxyClient("https://proxy.test", "acme/shop/orders").env(self.ctx())

        self.assertIn("organization manager", str(caught.exception))
        self.assertIn("org.manage is required", str(caught.exception))

    def test_an_old_proxy_is_refused(self):
        fake = FakeProxy({}, version="0.0.1")

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(DeployError) as caught:
                ProxyClient("https://proxy.test", "acme/shop/orders").env(self.ctx())

        self.assertIn("update the proxy", str(caught.exception))

    def test_the_platform_can_name_the_proxy_and_the_app(self):
        (self.root / "samconfig.toml").write_text(
            SAMCONFIG.format(stack="ap-acme-shop-orders-dev")
        )
        (self.root / "template.yaml").write_text("Resources: {}\n")
        fake = FakeProxy({"POST /apps/acme/shop/orders/credentials": GRANTED})
        ctx = self.ctx()
        ctx.env = {
            "AP_AWS_LAMBDA_PROXY_URL": "https://proxy.test",
            "AP_APP": "acme/shop/orders",
        }

        with mock.patch("urllib.request.urlopen", fake):
            env = LambdaTarget().env(ctx)

        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIA")
        self.assertEqual(fake.requests[-1][1], "/apps/acme/shop/orders/credentials")

    def test_app_must_be_three_parts(self):
        with self.assertRaises(DeployError):
            ProxyClient("https://proxy.test", "orders")

    def test_the_target_refuses_a_stack_outside_the_prefix(self):
        (self.root / "samconfig.toml").write_text(SAMCONFIG.format(stack="shop-dev"))
        (self.root / "template.yaml").write_text("Resources: {}\n")
        fake = FakeProxy({"POST /apps/acme/shop/orders/credentials": GRANTED})
        target = LambdaTarget(proxy_url="https://proxy.test", app="acme/shop/orders")

        with (
            mock.patch("urllib.request.urlopen", fake),
            mock.patch.multiple(
                shell,
                run=lambda *a, **k: "{}",
                require=lambda tool, hint: [f"/usr/bin/{tool}"],
            ),
        ):
            with self.assertRaises(DeployError) as caught:
                target.preflight(self.ctx())

        self.assertIn("must start with 'ap-acme-shop-orders'", str(caught.exception))

    def test_the_target_deploys_with_the_granted_credentials(self):
        (self.root / "samconfig.toml").write_text(
            SAMCONFIG.format(stack="ap-acme-shop-orders-dev")
        )
        (self.root / "template.yaml").write_text("Resources: {}\n")
        fake = FakeProxy({"POST /apps/acme/shop/orders/credentials": GRANTED})
        target = LambdaTarget(proxy_url="https://proxy.test", app="acme/shop/orders")
        seen: list[dict | None] = []
        calls: list[list[str]] = []

        def run(args, cwd=None, env=None):
            seen.append(env)
            calls.append(args)

            return "{}"

        with (
            mock.patch("urllib.request.urlopen", fake),
            mock.patch.multiple(
                shell, run=run, require=lambda tool, hint: [f"/usr/bin/{tool}"]
            ),
        ):
            target.preflight(self.ctx())
            target.deploy(self.ctx())

        self.assertTrue(all(e and e["AWS_SESSION_TOKEN"] == "t" for e in seen))
        self.assertEqual(len([r for r in fake.requests if r[0] == "POST"]), 1)
        deploy = next(c for c in calls if c[1] == "deploy")
        self.assertEqual(
            deploy[-2:],
            [
                "--parameter-overrides",
                "ExecutionRoleArn=arn:aws:iam::1:role/action-platform/ap-exec-acme-shop-orders",
            ],
        )
