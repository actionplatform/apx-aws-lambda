# apx-aws-lambda

AWS Lambda for [Action Platform](https://github.com/actionplatform/action-platform): the `aws/lambda` deploy target (SAM) with its overlay, read-only tools and commands. `apx-` is the prefix every Action Platform extension carries.

```bash
action-platform plugin install aws-lambda
action-platform cloud set aws/lambda        # overlay files come from this plugin
action-platform deploy --dry-run            # preflight: sam validate, credentials
action-platform deploy                      # sam build + sam deploy --config-env <stage>
action-platform diagnose
```

| | |
|---|---|
| Overlay | `template.yaml`, `samconfig.toml`, `lambda_handler.py`, `Makefile`, `requirements/` (IAM), `.github/workflows/deploy.yml` |
| Deploy | `sam build` + `sam deploy --config-env <stage>` (`dev` → `default`, `prod` → `prod`) |
| Rollback | CloudFormation `rollback-stack` — previous stack state |
| Diagnose | stack status and the HTTP API url |
| Destroy | `sam delete` |

`[deploy]` in platform.toml:

```toml
[deploy]
target = "aws/lambda"
region = "us-east-1"          # optional; samconfig.toml / AWS_REGION otherwise
```

Tools (`action-platform mcp`): `aws_lambda_stacks`, `aws_lambda_functions`. Commands: `action-platform aws-lambda stacks|functions`. Deploying itself goes through the core's `deploy` / `rollback` / `diagnose`, which drive the target.

Needs: AWS CLI v2 (`aws`), SAM CLI (`sam`), credentials in the environment (`AWS_PROFILE` or keys), `AWS_REGION`. Talks to `*.amazonaws.com` only.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests fake `aws` and `sam`; nothing reaches AWS.
