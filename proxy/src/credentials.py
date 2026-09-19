"""Short-lived credentials of an app's deploy role, assumed by the proxy's function role."""

from __future__ import annotations

import re
import time
from typing import Any

from botocore.exceptions import ClientError
from settings import ASSUME_ATTEMPTS, ASSUME_BACKOFF, MAX_DURATION, MIN_DURATION


class Credentials:
    def __init__(self, sts: Any) -> None:
        self.sts = sts

    @staticmethod
    def duration(wanted: Any) -> int:
        return max(MIN_DURATION, min(MAX_DURATION, int(wanted or MIN_DURATION)))

    @staticmethod
    def session_name(actor: str | None) -> str:
        return re.sub(r"[^\w+=,.@-]", "-", actor or "platform")[:64]

    def assume(self, role_arn: str, session: str, duration: int) -> dict:
        """IAM takes a few seconds to let a role just created be assumed; the first deploy of an app hits exactly that window."""
        for attempt in range(ASSUME_ATTEMPTS):
            try:
                return self.sts.assume_role(
                    RoleArn=role_arn, RoleSessionName=session, DurationSeconds=duration
                )["Credentials"]
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")

                if code != "AccessDenied" or attempt == ASSUME_ATTEMPTS - 1:
                    raise

                time.sleep(ASSUME_BACKOFF)

        raise RuntimeError("unreachable")
