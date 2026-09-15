"""The targets drive `aws` and `sam`; here both are fakes, so what is asserted is the sequence and the answers."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from action_platform.core.context import Context
from action_platform.core.exception import DeployError
from action_platform.core.scaffold.templates import Matrix, with_plugin_clouds
from action_platform.plugins import Loaded, PluginState, Plugins, registry

from action_platform_plugin_aws import AwsPlugin, shell
from action_platform_plugin_aws.amplify import AmplifyTarget
from action_platform_plugin_aws.lambda_ import LambdaTarget

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
            shell, run=run, require=lambda tool, hint: f"/usr/bin/{tool}"
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


class AmplifyTargetTest(unittest.TestCase):
    def test_deploy_starts_a_job_and_waits(self):
        answers = {
            "amplify start-job": {"jobSummary": {"jobId": "7"}},
            "amplify get-job": {"job": {"summary": {"status": "SUCCEED"}}},
            "amplify get-app": {"app": {"defaultDomain": "d1.amplifyapp.com"}},
        }
        calls = []

        def run(args, cwd=None, env=None):
            calls.append(args[1:])

            return json.dumps(answers.get(" ".join(args[1:3]), {}))

        with mock.patch.multiple(shell, run=run, require=lambda t, h: "/usr/bin/aws"):
            result = AmplifyTarget(app_id="d1", timeout=5).deploy(
                Context(repo_root=Path("."), branch="main", next_version="2.0.0")
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.url, "https://main.d1.amplifyapp.com")
        self.assertTrue(any(c[:2] == ["amplify", "start-job"] for c in calls))

    def test_needs_an_app_id(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            self.assertRaises(DeployError),
        ):
            AmplifyTarget().preflight(Context(repo_root=Path(".")))


class PluginTest(unittest.TestCase):
    def test_overlays_and_tools(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        plugins = Plugins(
            [Loaded(AwsPlugin(), "action-platform-plugin-aws", "0.1.0")],
            PluginState(file=Path(tmp.name) / "p.json"),
        )
        registry._current = plugins
        self.addCleanup(registry.reset)

        merged = with_plugin_clouds(
            Matrix.from_dict({"clouds": [{"id": "aws/lambda", "description": "old"}]})
        )

        self.assertEqual({c.name for c in merged.clouds}, {"aws/lambda", "aws/amplify"})
        self.assertEqual(merged.cloud("aws/lambda").source, "aws")
        self.assertTrue(
            (
                merged.cloud("aws/lambda").root / "cloud/aws/lambda/cookiecutter.json"
            ).exists()
        )

        import asyncio

        from action_platform.mcp import server

        names = {t.name for t in asyncio.run(server.build().list_tools())}

        self.assertLessEqual({"aws.stacks", "aws.functions", "aws.amplify_jobs"}, names)
