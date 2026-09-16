# Deploy — AWS Lambda (SAM)

Overlay `cloud/aws/lambda`. The app runs on Lambda (arm64) behind an HTTP API Gateway; `sam build` follows the `Makefile` the overlay left for the language:

| Language | Runtime | How the app is served |
|---|---|---|
| Python | `python3.12` | `lambda_handler.py` — Mangum wraps `app.create_app()`; dependencies build into a layer from wheels |
| Node | `nodejs22.x` | the Lambda Web Adapter layer runs `run.sh` (`node dist/server.js`) and proxies HTTP to port 8080 |
| Java, Kotlin | `java17` | the Lambda Web Adapter layer runs `run.sh` (`java -jar app.jar`), readiness on `/health` |
| Go | `provided.al2023` | `cmd/lambda/main.go` (`-tags lambda`) — the gin engine behind `aws-lambda-go-api-proxy`, one static `bootstrap` |
| Ruby | `ruby3.3` | `lambda_handler.rb` — the Rack app from `app.rb` called straight from the API Gateway event, no server |

The route is `/health` — API Gateway reserves `/ping` on `execute-api` and answers it itself.

## Local

```bash
pip install aws-sam-cli
sam build
sam local start-api            # http://127.0.0.1:3000/health
sam deploy                     # dev stack
sam deploy --config-env prod   # prod stack
```

Through the platform's deploy proxy the stack is named `ap-<org>-<project>-<app>-<dev|prod>` and `--stack-name` is set by the target; `samconfig.toml`'s `stack_name` only matters for a deploy from a machine.

## CI

`.github/workflows/deploy.yml` deploys on push: `develop` → dev, `master`/`main` → prod. Per environment: secret `AWS_DEPLOY_ROLE_ARN` (OIDC role); optional `DOMAIN_NAME` var + `DOMAIN_CERTIFICATE_ARN` secret for a custom domain. The toolchain comes from `actionplatform/ci-github/setup` (reads `platform.toml`).

## Files

```
template.yaml        # SAM resources: function, HTTP API (a deps layer for Python)
samconfig.toml       # dev / prod stacks
Makefile             # SAM build recipes for the language
run.sh | lambda_handler.* | cmd/lambda/   # how the language's app is started on Lambda
```

## Requirements — IAM

`requirements/` declares the least privilege the deploy role needs. Create it once per account:

```bash
sed -i "s/ACCOUNT_ID/$(aws sts get-caller-identity --query Account --output text)/g" requirements/*.json
aws iam create-role --role-name {{ cookiecutter.project_slug }}-deploy --assume-role-policy-document file://requirements/trust.json
aws iam put-role-policy --role-name {{ cookiecutter.project_slug }}-deploy --policy-name deploy --policy-document file://requirements/policy.json
```

`trust.json` trusts GitHub OIDC for this repo only. Put the role ARN in the `AWS_DEPLOY_ROLE_ARN` secret.
