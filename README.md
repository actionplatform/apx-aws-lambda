# action-platform-plugin-aws

AWS for [Action Platform](https://github.com/actionplatform/action-platform): two deploy targets with their overlays, read-only tools and commands.

```bash
action-platform plugin install aws
action-platform cloud set aws/lambda        # or aws/amplify — overlay files come from this plugin
action-platform deploy                      # preflight; --no-dry-run to ship
action-platform diagnose
```

| Target | Overlay | Deploy | Rollback | Diagnose |
|---|---|---|---|---|
| `aws/lambda` | `template.yaml`, `samconfig.toml`, `lambda_handler.py`, `Makefile`, `requirements/` (IAM), `deploy.yml` | `sam build` + `sam deploy --config-env <stage>` (`dev` → `default`, `prod` → `prod`) | CloudFormation `rollback-stack` (previous stack state) | stack status and the HTTP API url |
| `aws/amplify` | `amplify.yml`, `customHttp.yml`, `requirements/`, `deploy.yml` | `amplify start-job RELEASE` on the current branch, waits | retry of the previous succeeded job | last job of the branch and its url |

`[deploy]` in platform.toml:

```toml
[deploy]
target = "aws/lambda"
region = "us-east-1"          # optional; samconfig.toml / AWS_REGION otherwise

[deploy]
target = "aws/amplify"
app_id = "d1abc2def3"         # or AMPLIFY_APP_ID
```

Tools (`action-platform mcp`): `aws.stacks`, `aws.functions`, `aws.amplify_jobs`. Commands: `action-platform aws stacks|functions|jobs`. Deploying itself goes through the core's `deploy` / `rollback` / `diagnose`, which drive these targets.

Needs: AWS CLI v2 (`aws`), SAM CLI (`sam`) for Lambda, credentials in the environment (`AWS_PROFILE` or keys), `AWS_REGION`. Talks to `*.amazonaws.com` only.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests fake `aws` and `sam`; nothing reaches AWS.
