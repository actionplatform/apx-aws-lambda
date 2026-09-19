# Deploy — AWS Lambda (SAM)

Overlay `cloud/aws/lambda`. The app runs on Lambda (arm64) behind an HTTP API Gateway, the same way in every language: the **Lambda Web Adapter** layer starts the app as an HTTP server on `$PORT` (8080) and proxies the API Gateway events to it. `sam build` follows the one-line `Makefile` the overlay left — `ap-build package` — which assembles the app, its dependencies and `run.sh` (`exec <start command>`) into the function's artifact:

| Language | Runtime | `ap-build package` | `run.sh` |
|---|---|---|---|
| Python | `python3.12` | the project's wheel and its dependencies, built for arm64 | `uvicorn app:app --port $PORT` |
| Node | `nodejs22.x` | `dist/` and the pruned `node_modules` | `node dist/server.js` |
| Java, Kotlin | `java21` | `target/*.jar` as `app.jar` | `java -jar app.jar --server.port=$PORT` |
| Go | `provided.al2023` | one static binary named `bootstrap` from `./cmd/server` | — the binary itself; the adapter runs as an extension |
| Ruby | `ruby3.3` | the app and `vendor/bundle` without development/test gems | `rackup -s webrick -p $PORT` |

`[build] start` (or `[build] package`, `[build] main`) in `platform.toml` overrides the language's default. `ap-build` comes with the platform's worker and with the `build-<language>` images; `pip install ap-build` puts it on a machine.

The route is `/health` — API Gateway reserves `/ping` on `execute-api` and answers it itself.

## Local

```bash
pip install aws-sam-cli
sam build
sam local start-api            # http://127.0.0.1:3000/health
sam deploy                     # dev stack
sam deploy --config-env prod   # prod stack
```

Through the platform's deploy proxy the stack is named `ap-<org>-<project>-<app>-<scope>` (`Stage` takes the scope's name) and `--stack-name` is set by the target; `samconfig.toml`'s `stack_name` only matters for a deploy from a machine.

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
