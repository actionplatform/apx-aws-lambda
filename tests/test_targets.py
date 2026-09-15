"""The target drives `aws` and `sam`; here both are fakes, so what is asserted is the sequence and the answers."""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from action_platform.core.context import Context
from action_platform.core.exception import DeployError
from action_platform.core.scaffold.templates import Matrix, with_plugin_clouds
from action_platform.plugins import Loaded, Plugins, PluginState, registry
from action_platform.settings import settings

from apx_aws_lambda import AwsLambdaPlugin, shell
from apx_aws_lambda.lambda_ import LambdaTarget

SAMCONFIG = """version = 0.1
[default.deploy.parameters]
stack_name = "shop-dev"
region = "us-east-1"
[prod.deploy.parameters]
stack_name = "shop-prod"
region = "us-east-1"
"""


class LambdaTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "samconfig.toml").write_text(SAMCONFIG)
        (self.root / "template.yaml").write_text("Resources: {}\n")
        self.calls: list[list[str]] = []
        self.addCleanup(self.tmp.cleanup)

    def fake(self, answers: dict[str, dict] | None = None):
        answers = answers or {}

        def run(args, cwd=None, env=None):
            self.calls.append([Path(args[0]).name, *args[1:]])
            key = " ".join(args[1:3])

            return json.dumps(answers.get(key, {}))

        return mock.patch.multiple(
            shell, run=run, require=lambda tool, hint: [f"/usr/bin/{tool}"]
        )

    def ctx(self, stage="dev"):
        return Context(
            repo_root=self.root, branch="develop", next_version="1.2.0", stage=stage
        )

    def test_preflight_validates_and_deploy_builds_then_deploys_the_stage(self):
        target = LambdaTarget()

        with self.fake(
            {
                "cloudformation describe-stacks": {
                    "Stacks": [
                        {
                            "StackStatus": "UPDATE_COMPLETE",
                            "Outputs": [
                                {"OutputKey": "ApiUrl", "OutputValue": "https://x"}
                            ],
                        }
                    ]
                }
            }
        ):
            target.preflight(self.ctx())
            result = target.deploy(self.ctx("prod"))

        self.assertIn(["sam", "validate", "--lint"], self.calls)
        self.assertIn(["sam", "build"], self.calls)
        self.assertIn(
            [
                "sam",
                "deploy",
                "--no-confirm-changeset",
                "--no-fail-on-empty-changeset",
                "--config-env",
                "prod",
            ],
            self.calls,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.url, "https://x")
        self.assertEqual(result.version, "1.2.0")

    def test_diagnose_reads_the_stack(self):
        with self.fake(
            {
                "cloudformation describe-stacks": {
                    "Stacks": [{"StackStatus": "ROLLBACK_COMPLETE", "Outputs": []}]
                }
            }
        ):
            d = LambdaTarget().diagnose(self.ctx())

        self.assertFalse(d.ok)
        self.assertEqual(d.status, "ROLLBACK_COMPLETE")
        self.assertEqual(d.details["stack"], "shop-dev")

    def test_without_the_overlay_it_says_so(self):
        (self.root / "samconfig.toml").unlink()

        with self.fake(), self.assertRaises(DeployError) as caught:
            LambdaTarget().preflight(self.ctx())

        self.assertIn("overlay", str(caught.exception))


class PluginTest(unittest.TestCase):
    def test_overlays_and_tools(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        plugins = Plugins(
            [Loaded(AwsLambdaPlugin(), "apx-aws-lambda", "0.1.0")],
            PluginState(file=Path(tmp.name) / "p.json"),
        )
        registry._current = plugins
        self.addCleanup(registry.reset)

        merged = with_plugin_clouds(
            Matrix.from_dict({"clouds": [{"id": "aws/lambda", "description": "old"}]})
        )

        self.assertEqual({c.name for c in merged.clouds}, {"aws/lambda"})
        self.assertEqual(merged.cloud("aws/lambda").source, "aws-lambda")
        self.assertTrue(
            (
                merged.cloud("aws/lambda").root / "cloud/aws/lambda/cookiecutter.json"
            ).exists()
        )

        import asyncio

        from action_platform.mcp import server

        names = {t.name for t in asyncio.run(server.build().list_tools())}

        self.assertLessEqual({"aws_lambda_stacks", "aws_lambda_functions"}, names)


class AssumeRoleTest(unittest.TestCase):
    def test_role_arn_turns_the_platform_token_into_temporary_credentials(self):
        calls: list[list[str]] = []

        def run(args, cwd=None, env=None):
            calls.append([Path(args[0]).name, *args[1:]])
            if args[1:3] == ["sts", "assume-role-with-web-identity"]:
                return json.dumps(
                    {
                        "Credentials": {
                            "AccessKeyId": "AKIA",
                            "SecretAccessKey": "s",
                            "SessionToken": "t",
                        }
                    }
                )
            return json.dumps(
                {"Stacks": [{"StackStatus": "UPDATE_COMPLETE", "Outputs": []}]}
            )

        ctx = Context(repo_root=Path("."), identity=lambda aud: f"jwt-for-{aud}")

        with mock.patch.multiple(
            shell, run=run, require=lambda t, h: [f"/usr/bin/{t}"]
        ):
            env = LambdaTarget(
                role_arn="arn:aws:iam::1:role/deploy", region="us-east-1"
            ).env(ctx)

        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIA")
        self.assertEqual(env["AWS_SESSION_TOKEN"], "t")
        sts = next(
            c for c in calls if c[1:3] == ["sts", "assume-role-with-web-identity"]
        )
        self.assertIn("jwt-for-sts.amazonaws.com", sts)
        self.assertIn("arn:aws:iam::1:role/deploy", sts)

    def test_without_an_issuer_or_a_login_it_says_so(self):
        with (
            mock.patch.object(shell, "require", lambda t, h: "/usr/bin/aws"),
            mock.patch.dict("os.environ", {"AP_HOME": "/nonexistent"}, clear=False),
        ):
            with self.assertRaises(DeployError) as caught:
                LambdaTarget(role_arn="arn:aws:iam::1:role/deploy").env(
                    Context(repo_root=Path("."))
                )

        self.assertIn("log in", str(caught.exception))


class ModuleEnvTest(unittest.TestCase):
    def test_the_plugins_volume_reaches_a_python_m_child(self):
        with mock.patch.object(settings, "PLUGINS_DIR", Path("/data/plugins")):
            env = shell.module_env([sys.executable, "-m", "awscli"], {"PATH": "/bin"})
            same = shell.module_env(["/usr/bin/aws"], {"PATH": "/bin"})

        self.assertEqual(env["PYTHONPATH"], "/data/plugins")
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(same, {"PATH": "/bin", "SAM_CLI_TELEMETRY": "0"})
