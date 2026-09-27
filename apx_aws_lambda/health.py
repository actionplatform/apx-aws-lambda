"""After CloudFormation finishes, the function has to answer: a GET on its health route through the HTTP API, retried while Lambda cold-starts it."""

from __future__ import annotations

import time
from urllib import error, request

from apx_aws_lambda.abc import Health

ATTEMPTS = 12
PAUSE = 5
TIMEOUT = 15


class HttpHealth(Health):
    def answers(self, url: str) -> str | None:
        last = "no answer"

        for attempt in range(ATTEMPTS):
            status = get(url)

            if 200 <= status < 300:
                return None

            last = f"answered {status}" if status else "no answer"

            if attempt < ATTEMPTS - 1:
                time.sleep(PAUSE)

        return f"{url} {last} after {ATTEMPTS * PAUSE}s"


def get(url: str) -> int:
    """The status of a GET on `url`; 0 when nothing answered."""
    try:
        with request.urlopen(request.Request(url), timeout=TIMEOUT) as response:
            return response.status
    except error.HTTPError as e:
        return e.code
    except (error.URLError, TimeoutError, OSError):
        return 0
