"""Readiness, one class per check, run in the order the target lists them: tooling, the overlay, the stack name, the credentials, the boundary, the stack's state, the deploy role's permissions, the template."""

from __future__ import annotations

import re

from action_platform.core.context import Check
from action_platform.core.exception import DeployError

from apx_aws_lambda import shell
from apx_aws_lambda.abc import Access, Readiness, Sam, Stack
from apx_aws_lambda.spec import PREFIX_TAG, Spec
from apx_aws_lambda.stack import FAILED_CREATION


class ToolCheck(Readiness):
    def __init__(self, tool: str, hint: str) -> None:
        self.tool = tool
        self.hint = hint
        self.id = f"tool.{tool}"

    def run(self, spec: Spec) -> Check:
        try:
            shell.require(self.tool, self.hint)
        except DeployError as e:
            return Check(self.id, False, str(e), fix=self.hint)

        return Check(self.id, True, "installed")


class TemplateCheck(Readiness):
    id = "template.present"

    def run(self, spec: Spec) -> Check:
        if spec.template.exists():
            return Check(self.id, True, "template.yaml is there")

        return Check(
            self.id,
            False,
            "template.yaml not found",
            fix="apply the aws/lambda overlay: action-platform cloud set aws/lambda",
        )


class StackNameCheck(Readiness):
    id = "stack.name"

    def run(self, spec: Spec) -> Check:
        try:
            return Check(self.id, True, spec.stack)
        except DeployError as e:
            return Check(self.id, False, str(e), fix="fill samconfig.toml")


class CredentialsCheck(Readiness):
    """Who the deploy acts as; `identity` keeps the answer for the permissions check."""

    id = "aws.credentials"

    def __init__(self, access: Access) -> None:
        self.access = access
        self.identity: dict = {}

    def run(self, spec: Spec) -> Check:
        try:
            self.identity = shell.aws(
                "sts", "get-caller-identity", region=spec.region, env=self.access.env
            )
        except DeployError as e:
            return Check(
                self.id,
                False,
                str(e),
                fix="connect the AWS account in the AWS Lambda plugin",
            )

        return Check(self.id, True, self.identity.get("Arn", "credentials work"))


class BoundaryCheck(Readiness):
    id = "template.boundary"

    def run(self, spec: Spec) -> Check:
        if spec.takes_boundary:
            return Check(self.id, True, "the execution role takes the boundary")

        return Check(
            self.id,
            False,
            "template.yaml lacks the PermissionsBoundaryArn or RolePath parameter: a connected account creates the execution role only within its boundary, under the app's path",
            fix="apply the aws/lambda overlay again: action-platform cloud set aws/lambda",
        )


class StackStateCheck(Readiness):
    id = "stack.state"

    def __init__(self, stack: Stack) -> None:
        self.stack = stack

    def run(self, spec: Spec) -> Check:
        status = self.stack.status()

        if status is None:
            return Check(self.id, True, "no stack yet; the deploy creates it")

        if status in FAILED_CREATION:
            return Check(
                self.id,
                True,
                f"{status}: the failed first creation is deleted before the deploy",
                severity="warning",
            )

        if status.endswith("_IN_PROGRESS"):
            return Check(
                self.id,
                False,
                f"{status}: another operation is running on the stack",
                fix="wait for it to finish, or cancel it in CloudFormation",
            )

        if status.endswith("_FAILED"):
            return Check(
                self.id,
                False,
                status,
                fix="the stack needs attention in CloudFormation before a deploy can update it",
            )

        return Check(self.id, True, status)


class PermissionsCheck(Readiness):
    """The deploy role simulated against what the template needs — for a connected account with the session tag, the boundary and the service the execution role goes to as context."""

    id = "aws.permissions"

    def __init__(self, access: Access, credentials: CredentialsCheck) -> None:
        self.access = access
        self.credentials = credentials

    def run(self, spec: Spec) -> Check | None:
        identity = self.credentials.identity
        role = self.access.deploy_role or role_of(identity.get("Arn", ""))

        if not role:
            return None

        stack = spec.stack
        account = identity.get("Account", "")
        region = spec.region or "us-east-1"
        wanted: list[tuple[list[str], list[str]]] = [
            (
                ["cloudformation:CreateChangeSet", "cloudformation:DescribeStacks"],
                [f"arn:aws:cloudformation:{region}:{account}:stack/{stack}/*"],
            ),
            (
                ["lambda:CreateFunction", "lambda:UpdateFunctionCode"],
                [f"arn:aws:lambda:{region}:{account}:function:{stack}-ApiFunction"],
            ),
            (
                ["logs:CreateLogGroup"],
                [
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{stack}-ApiFunction"
                ],
            ),
        ]
        layers = layers_of(spec, region)

        if layers:
            wanted.append((["lambda:GetLayerVersion"], layers))

        context: list[str] = []

        if spec.connected:
            execution = f"arn:aws:iam::{account}:role{spec.connected.role_path}{stack}-ApiFunctionRole"
            wanted.append((["iam:CreateRole", "iam:PassRole"], [execution]))
            context = [
                "--context-entries",
                f"ContextKeyName=aws:PrincipalTag/{PREFIX_TAG},ContextKeyValues={spec.connected.prefix},ContextKeyType=string",
                f"ContextKeyName=iam:PermissionsBoundary,ContextKeyValues={spec.connected.boundary},ContextKeyType=string",
                "ContextKeyName=iam:PassedToService,ContextKeyValues=lambda.amazonaws.com,ContextKeyType=string",
            ]

        denied: list[str] = []

        for actions, resources in wanted:
            try:
                data = shell.aws(
                    "iam",
                    "simulate-principal-policy",
                    "--policy-source-arn",
                    role,
                    "--action-names",
                    *actions,
                    "--resource-arns",
                    *resources,
                    *context,
                    env=self.access.env,
                )
            except DeployError as e:
                return Check(
                    self.id,
                    True,
                    f"not simulated: {str(e)[:200]}",
                    severity="warning",
                )

            for row in data.get("EvaluationResults") or []:
                if row.get("EvalDecision") != "allowed":
                    denied.append(
                        f"{row.get('EvalActionName')} on {row.get('EvalResourceName')}"
                    )

        if denied:
            return Check(
                self.id,
                False,
                f"{role} may not: " + "; ".join(denied),
                fix="widen the deploy role's policy (update the connect stack when it created the role)",
            )

        return Check(self.id, True, f"{role} may deploy {stack}")


class TemplateValidCheck(Readiness):
    id = "template.valid"

    def __init__(self, sam: Sam) -> None:
        self.sam = sam

    def run(self, spec: Spec) -> Check:
        try:
            self.sam.validate()
        except DeployError as e:
            return Check(self.id, False, str(e), fix="fix template.yaml")

        return Check(self.id, True, "sam validate --lint passes")


def role_of(arn: str) -> str | None:
    found = re.match(r"^arn:aws:sts::(\d+):assumed-role/([^/]+)/", arn)

    return f"arn:aws:iam::{found.group(1)}:role/{found.group(2)}" if found else None


def layers_of(spec: Spec, region: str) -> list[str]:
    text = spec.template.read_text(errors="replace").replace("${AWS::Region}", region)

    return sorted(
        set(re.findall(r"arn:aws:lambda:[\w-]+:\d{12}:layer:[\w-]+:\d+", text))
    )
