"""The responsibilities an aws/lambda deploy is made of, one contract each; `LambdaTarget` composes an implementation of every one."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from action_platform.core.context import Check

    from apx_aws_lambda.spec import Spec


@dataclass(frozen=True)
class Access:
    """How `aws` and `sam` run: `env` holds temporary credentials (None: the caller's own), `deploy_role` the role they act as when it is known up front."""

    env: dict[str, str] | None
    deploy_role: str | None = None


class Credentials(ABC):
    """Which AWS identity the deploy acts as."""

    @abstractmethod
    def access(self, spec: Spec) -> Access: ...


class Stack(ABC):
    """The scope's CloudFormation stack."""

    @abstractmethod
    def status(self) -> str | None:
        """Its status, or None when there is no stack."""

    @abstractmethod
    def describe(self) -> dict | None:
        """The stack as CloudFormation describes it (status, outputs), or None."""

    @abstractmethod
    def clear_failed_creation(self) -> None:
        """Delete a stack left by a first creation that failed, so the deploy can create it again."""

    @abstractmethod
    def rollback(self) -> None: ...

    @abstractmethod
    def failure(self) -> str | None:
        """Why the latest operation failed: each FAILED resource and its reason."""


class Alias(ABC):
    """The function's `live` alias, the one the HTTP API invokes: which version it serves and how traffic splits between two."""

    @abstractmethod
    def read(self) -> dict:
        """`{"version": primary, "weights": {version: share}}`; raises DeployError when the alias does not exist."""

    @abstractmethod
    def point(self, version: str, weights: dict[str, float] | None = None) -> None:
        """Serve `version`, sending each `weights` version its share (0 to 1) of the invocations."""

    @abstractmethod
    def versions(self) -> list[str]:
        """Published versions, oldest first."""

    @abstractmethod
    def delete_version(self, version: str) -> None: ...

    @abstractmethod
    def errors(self, version: str, window: int) -> dict[str, float]:
        """Errors, Throttles and Invocations of `version` over the last `window` seconds."""


class Sam(ABC):
    """The SAM CLI on the repository."""

    @abstractmethod
    def build(self) -> None: ...

    @abstractmethod
    def deploy(self) -> None: ...

    @abstractmethod
    def delete(self) -> None: ...

    @abstractmethod
    def validate(self) -> None: ...


class Health(ABC):
    """Whether the deployed application answers."""

    @abstractmethod
    def answers(self, url: str) -> str | None:
        """None when `url` answers 2xx in time; otherwise what it did instead."""


class Readiness(ABC):
    """One check a deploy needs to pass, run without building or changing anything."""

    id: str

    @abstractmethod
    def run(self, spec: Spec) -> Check: ...
