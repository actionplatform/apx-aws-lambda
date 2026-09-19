"""The apps the proxy knows, one DynamoDB row each: region, who may deploy, which policy version the deploy role carries."""

from __future__ import annotations

import time
from typing import Any, Optional

from apps import App
from errors import Refused
from settings import POLICY_VERSION


class Registry:
    def __init__(self, table: Any) -> None:
        self.table = table

    def get(self, app: App) -> Optional[dict]:
        return self.table.get_item(Key={"app": app.key}).get("Item")

    def require(self, app: App) -> dict:
        row = self.get(app)

        if row is None:
            raise Refused(404, f"no app {app.key}; create it first")

        return row

    def register(self, app: App, region: str, actor: str) -> dict:
        row = self.get(app) or {}
        item = {
            "app": app.key,
            "region": region,
            "subjects": row.get("subjects") or [app.subject],
            "created_at": row.get("created_at") or int(time.time()),
            "created_by": row.get("created_by") or actor,
            "policy": POLICY_VERSION,
        }
        self.table.put_item(Item=item)

        return item

    def mark_policy(self, row: dict) -> None:
        self.table.put_item(Item={**row, "policy": POLICY_VERSION})

    def set_subjects(self, app: App, subjects: list[str]) -> None:
        self.table.update_item(
            Key={"app": app.key},
            UpdateExpression="SET subjects = :s",
            ExpressionAttributeValues={":s": sorted(set(subjects))},
        )

    def forget(self, app: App) -> None:
        self.table.delete_item(Key={"app": app.key})

    @staticmethod
    def granted(row: dict, subject: str) -> bool:
        return any(
            subject == allowed or subject.startswith(allowed + ":")
            for allowed in row.get("subjects") or []
        )
