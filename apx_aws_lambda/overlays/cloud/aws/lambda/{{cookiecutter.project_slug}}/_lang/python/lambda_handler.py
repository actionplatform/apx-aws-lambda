"""AWS Lambda entry point — adapts the ASGI app from `app.create_app()` with Mangum.

The app is rebuilt per invocation with the lifespan run each time: an MCP
session manager refuses to start twice on one instance, and the rebuild costs
milliseconds. Stateless transports are the app's own concern (see create_app).
"""

import os

from app import create_app
from mangum import Mangum


def lambda_handler(event, context):
    adapter = Mangum(
        create_app(),
        lifespan="auto",
        api_gateway_base_path=os.environ.get("API_STAGE") or "/",
    )
    return adapter(event, context)
