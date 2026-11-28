"""The strategy operations drive `aws lambda` and `aws cloudwatch`; here the alias is a small stateful fake."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from action_platform.core.context import Context
from action_platform.core.rollout.operations import OperationContext
from action_platform.core.rollout.support import support
from action_platform.testing.operations import contract_problems

from apx_aws_lambda import health, shell
from apx_aws_lambda.lambda_ import LambdaTarget

SAMCONFIG = """version = 0.1
[default.deploy.parameters]
stack_name = "shop-dev"
region = "us-east-1"
"""


class OperationsTest(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(health, "get", lambda url: 200),
            mock.patch.object(
                shell, "require", lambda tool, hint: [f"/usr/bin/{tool}"]
            ),
            mock.patch.object(shell, "run", self.fake_run),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "samconfig.toml").write_text(SAMCONFIG)
        (self.root / "template.yaml").write_text("Resources: {}\n")
        self.calls: list[list[str]] = []
        self.versions = ["1", "2", "3"]
        self.alias: dict | None = {"version": "1", "weights": {}}
        self.published = "2"
        self.metrics = {"Errors": 0, "Throttles": 0, "Invocations": 40}

    def fake_run(self, args, cwd=None, env=None):
        self.calls.append([Path(args[0]).name, *args[1:]])
        key = " ".join(args[1:3])

        if args[0].endswith("sam") and args[1] == "deploy" and self.alias:
            self.alias = {"version": self.published, "weights": {}}

        if key == "lambda get-alias":
            if self.alias is None:
                raise shell.DeployError("ResourceNotFoundException")

            row = {"FunctionVersion": self.alias["version"]}

            if self.alias["weights"]:
                row["RoutingConfig"] = {
                    "AdditionalVersionWeights": self.alias["weights"]
                }

            return json.dumps(row)

        if key == "lambda update-alias":
            version = args[args.index("--function-version") + 1]
            routing = json.loads(args[args.index("--routing-config") + 1])
            self.alias = {
                "version": version,
                "weights": routing["AdditionalVersionWeights"],
            }

            return "{}"

        if key == "lambda list-versions-by-function":
            return json.dumps(
                {
                    "Versions": [
                        {"Version": "$LATEST"},
                        *({"Version": v} for v in self.versions),
                    ]
                }
            )

        if key == "lambda delete-function":
            self.versions.remove(args[args.index("--qualifier") + 1])

            return "{}"

        if key == "cloudwatch get-metric-statistics":
            name = args[args.index("--metric-name") + 1]

            return json.dumps({"Datapoints": [{"Sum": self.metrics[name]}]})

        if key == "cloudformation describe-stacks":
            return json.dumps(
                {
                    "Stacks": [
                        {
                            "StackStatus": "UPDATE_COMPLETE",
                            "Outputs": [
                                {"OutputKey": "ApiUrl", "OutputValue": "https://x"}
                            ],
                        }
                    ]
                }
            )

        return "{}"

    def ops(self, **options):
        return LambdaTarget(**options).operations()

    def ctx(self, **fields):
        return OperationContext(
            app="acme/shop/orders",
            tag="v1.2.0",
            version="1.2.0",
            scope="dev",
            strategy="canary",
            deploy=Context(repo_root=self.root, stage="dev", next_version="1.2.0"),
            **fields,
        )

    def test_it_follows_the_operations_contract(self):
        self.assertEqual(contract_problems(self.ops(), self.ctx()), [])

    def test_every_strategy_but_shadow_is_supported(self):
        target = LambdaTarget()
        found = {
            s: support(target, s).supported
            for s in ("recreate", "rolling", "blue_green", "canary", "shadow")
        }

        self.assertEqual(
            found,
            {
                "recreate": True,
                "rolling": True,
                "blue_green": True,
                "canary": True,
                "shadow": False,
            },
        )
        self.assertIn("mirror", support(target, "shadow").reason)
        self.assertIn("unsupported", self.ops().mirror(self.ctx()).detail)

    def test_deploy_new_at_zero_publishes_a_version_and_keeps_the_old_one_serving(self):
        result = self.ops().deploy_new(self.ctx(params={"weight": 0}))

        self.assertTrue(result.ok)
        self.assertEqual(self.alias, {"version": "1", "weights": {"2": 0.0}})
        self.assertEqual(result.external_ref, "2")
        self.assertEqual(result.data["previous_version"], "1")
        self.assertIn(["sam", "deploy"], [c[:2] for c in self.calls])

    def test_deploy_new_at_full_weight_leaves_the_alias_on_the_new_version(self):
        result = self.ops().deploy_new(self.ctx(params={"weight": 100}))

        self.assertTrue(result.ok)
        self.assertEqual(self.alias, {"version": "2", "weights": {}})

    def test_a_failed_deploy_puts_the_old_version_back(self):
        with (
            mock.patch.object(health, "get", lambda url: 500),
            mock.patch.object(health, "ATTEMPTS", 1),
        ):
            result = self.ops().deploy_new(self.ctx(params={"weight": 0}))

        self.assertFalse(result.ok)
        self.assertEqual(self.alias["version"], "1")

    def test_shift_sets_the_routing_weight_of_the_new_version(self):
        self.alias = {"version": "1", "weights": {"2": 0.0}}
        result = self.ops().shift(self.ctx(params={"weight": 25}))

        self.assertTrue(result.ok)
        self.assertEqual(self.alias, {"version": "1", "weights": {"2": 0.25}})

    def test_shift_to_one_hundred_points_the_alias_at_the_new_version(self):
        self.alias = {"version": "1", "weights": {"2": 0.5}}
        self.ops().shift(self.ctx(params={"weight": 100}))

        self.assertEqual(self.alias, {"version": "2", "weights": {}})

    def test_replace_moves_the_share_up_to_the_batch(self):
        self.alias = {"version": "1", "weights": {"2": 0.0}}
        self.ops().replace(self.ctx(params={"batch_percent": 40, "upto": 40}))

        self.assertEqual(self.alias["weights"], {"2": 0.4})

    def test_retire_old_clears_the_routing_and_keeps_every_version_by_default(self):
        self.alias = {"version": "2", "weights": {"3": 0.1}}
        self.ops().retire_old(self.ctx(data={"new_version": "2"}))

        self.assertEqual(self.alias, {"version": "2", "weights": {}})
        self.assertEqual(self.versions, ["1", "2", "3"])

    def test_retire_old_deletes_the_oldest_versions_past_the_retention(self):
        self.versions = ["1", "2", "3", "4"]
        self.alias = {"version": "4", "weights": {}}
        result = self.ops(retain_versions=2).retire_old(self.ctx())

        self.assertTrue(result.ok)
        self.assertEqual(self.versions, ["3", "4"])
        self.assertEqual(result.data["deleted_versions"], ["1", "2"])

    def test_rollback_points_the_alias_back_to_the_serving_version(self):
        self.alias = {"version": "1", "weights": {"2": 0.5}}
        result = self.ops().rollback(self.ctx())

        self.assertTrue(result.ok)
        self.assertEqual(self.alias, {"version": "1", "weights": {}})

    def test_rollback_after_a_full_shift_goes_to_the_previous_version(self):
        self.alias = {"version": "3", "weights": {}}
        self.ops().rollback(self.ctx())

        self.assertEqual(self.alias, {"version": "2", "weights": {}})

    def test_rollback_with_nothing_earlier_says_so(self):
        self.versions = ["1"]
        self.alias = {"version": "1", "weights": {}}

        self.assertFalse(self.ops().rollback(self.ctx()).ok)

    def test_health_passes_a_version_without_errors_or_throttles(self):
        self.alias = {"version": "1", "weights": {"2": 0.1}}
        result = self.ops().health(self.ctx())

        self.assertTrue(result.ok)
        self.assertEqual(result.data["version"], "2")
        metric = next(
            c for c in self.calls if c[1:3] == ["cloudwatch", "get-metric-statistics"]
        )
        self.assertIn("Name=Resource,Value=shop-dev-api:2", metric)

    def test_health_fails_on_errors_past_the_threshold(self):
        self.alias = {"version": "1", "weights": {"2": 0.1}}
        self.metrics["Errors"] = 3
        result = self.ops(max_errors=2).health(self.ctx())

        self.assertFalse(result.ok)
        self.assertIn("3 errors (max 2)", result.detail)

    def test_health_fails_on_throttles(self):
        self.alias = {"version": "1", "weights": {"2": 0.1}}
        self.metrics["Throttles"] = 1

        self.assertIn("1 throttles", self.ops().health(self.ctx()).detail)

    def test_health_fails_when_the_health_url_does_not_answer(self):
        self.alias = {"version": "2", "weights": {}}

        with (
            mock.patch.object(health, "get", lambda url: 503),
            mock.patch.object(health, "ATTEMPTS", 1),
        ):
            result = self.ops().health(self.ctx())

        self.assertFalse(result.ok)
        self.assertIn("https://x/health", result.detail)

    def test_status_reports_the_alias_version_and_weights(self):
        self.alias = {"version": "1", "weights": {"2": 0.25}}
        result = self.ops().status(self.ctx())

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"version": "1", "weights": {"2": 0.25}})
        self.assertIn("2 25%", result.detail)

    def test_a_function_without_the_live_alias_asks_for_the_overlay(self):
        self.alias = None
        result = self.ops().status(self.ctx())

        self.assertFalse(result.ok)
        self.assertIn("apply the aws/lambda overlay again", result.detail)

    def test_the_overlay_publishes_the_live_alias(self):
        template = next(
            Path(__file__).parent.parent.glob(
                "apx_aws_lambda/overlays/cloud/aws/lambda/*/template.yaml"
            )
        )

        self.assertIn("AutoPublishAlias: live", template.read_text())

    def test_the_connect_stack_may_read_function_metrics(self):
        template = Path(__file__).parent.parent / "apx_aws_lambda/connect/template.yaml"

        self.assertIn("cloudwatch:GetMetricStatistics", template.read_text())
