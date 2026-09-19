"""The two IAM roles of an app — execution and deploy — created, kept in step, and removed by the proxy's function role."""

from __future__ import annotations

import json
import re
from typing import Any

from apps import App
from settings import LAMBDA_BASIC_EXECUTION, PATH, Settings

LAMBDA_TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


def role_of_identity(arn: str) -> str:
    return re.sub(
        r"^arn:aws:sts::(\d+):assumed-role/([^/]+)/.*$", r"arn:aws:iam::\1:role/\2", arn
    )


class Roles:
    def __init__(self, iam: Any, sts: Any, settings: Settings) -> None:
        self.iam = iam
        self.sts = sts
        self.settings = settings

    def proxy_role(self) -> str:
        return role_of_identity(self.sts.get_caller_identity()["Arn"])

    def create(self, app: App, region: str) -> None:
        self.ensure(
            app.role_name("exec"),
            trust=LAMBDA_TRUST,
            tags=app.tags(),
            boundary=self.settings.boundary_arn,
            managed=[LAMBDA_BASIC_EXECUTION],
        )
        self.ensure(
            app.role_name("deploy"),
            trust={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": self.proxy_role()},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            tags=app.tags(),
            inline={"deploy": app.deploy_policy(region)},
        )

    def refresh_policy(self, app: App, region: str) -> None:
        self.iam.put_role_policy(
            RoleName=app.role_name("deploy"),
            PolicyName="deploy",
            PolicyDocument=json.dumps(app.deploy_policy(region)),
        )

    def delete(self, app: App) -> None:
        for kind in ("deploy", "exec"):
            self.drop(app.role_name(kind))

    def ensure(
        self,
        name: str,
        trust: dict,
        tags: list[dict[str, str]],
        boundary: str | None = None,
        managed: list[str] | None = None,
        inline: dict[str, dict] | None = None,
    ) -> str:
        try:
            arn = self.iam.get_role(RoleName=name)["Role"]["Arn"]
            self.iam.update_assume_role_policy(
                RoleName=name, PolicyDocument=json.dumps(trust)
            )
            self.iam.tag_role(RoleName=name, Tags=tags)
        except self.iam.exceptions.NoSuchEntityException:
            kwargs: dict[str, Any] = {
                "RoleName": name,
                "Path": PATH,
                "AssumeRolePolicyDocument": json.dumps(trust),
                "Tags": tags,
            }

            if boundary:
                kwargs["PermissionsBoundary"] = boundary

            arn = self.iam.create_role(**kwargs)["Role"]["Arn"]

        for policy in managed or []:
            self.iam.attach_role_policy(RoleName=name, PolicyArn=policy)

        for policy_name, document in (inline or {}).items():
            self.iam.put_role_policy(
                RoleName=name,
                PolicyName=policy_name,
                PolicyDocument=json.dumps(document),
            )

        return arn

    def drop(self, name: str) -> None:
        """A role already gone is evaluated without its path by IAM, where the function may only look — so look first, then touch."""
        try:
            self.iam.get_role(RoleName=name)
        except self.iam.exceptions.NoSuchEntityException:
            return

        for policy in self.iam.list_attached_role_policies(RoleName=name)[
            "AttachedPolicies"
        ]:
            self.iam.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])

        for policy_name in self.iam.list_role_policies(RoleName=name)["PolicyNames"]:
            self.iam.delete_role_policy(RoleName=name, PolicyName=policy_name)

        self.iam.delete_role(RoleName=name)
