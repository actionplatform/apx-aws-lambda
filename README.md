# apx-aws-lambda

AWS Lambda for [Action Platform](https://github.com/actionplatform/action-platform): the `aws/lambda` deploy target (SAM), its overlay, read-only tools and commands, and a **deploy proxy** for your AWS account so that no AWS key ever lives on the platform, on a machine or in a repository. `apx-` is the prefix every Action Platform extension carries.

```bash
action-platform plugin install aws-lambda
action-platform cloud set aws/lambda        # overlay files come from this plugin
action-platform deploy --dry-run            # preflight: credentials, sam validate
action-platform deploy                      # sam build + sam deploy --config-env <stage>
action-platform diagnose
```

## Contents

- [What the plugin does](#what-the-plugin-does)
- [Credentials without keys](#credentials-without-keys)
  - [The deploy proxy (recommended)](#the-deploy-proxy-recommended)
  - [A role of your own (OIDC)](#a-role-of-your-own-oidc)
  - [The AWS CLI's own credentials](#the-aws-clis-own-credentials)
- [Where the deploy runs](#where-the-deploy-runs)
- [The proxy in detail](#the-proxy-in-detail)
- [Tools and commands](#tools-and-commands)
- [Requirements](#requirements)
- [Development](#development)

## What the plugin does

| | |
|---|---|
| Overlay | `template.yaml`, `samconfig.toml`, `lambda_handler.py`, `Makefile`, `requirements/` (IAM examples), `.github/workflows/deploy.yml` (optional) |
| Deploy | `sam build` + `sam deploy --config-env <stage>` (`dev` → `default`, `prod` → `prod`) |
| Rollback | CloudFormation `rollback-stack` — previous stack state |
| Diagnose | stack status and the HTTP API url |
| Destroy | `sam delete` of the stage's stack; when no stage is left, the app leaves the proxy too (roles and grant) — the token must carry `org.manage`, which a platform deploy by an organization manager does |
| Tools | `aws_lambda_stacks`, `aws_lambda_functions` |
| Commands | `action-platform aws-lambda stacks\|functions`, `action-platform aws-lambda proxy …` |

`[deploy]` in `platform.toml`:

```toml
[deploy]
target = "aws/lambda"
region = "us-east-1"          # optional; samconfig.toml / AWS_REGION otherwise
```

Deploying itself goes through the core's `deploy` / `rollback` / `diagnose`, which drive the target.

## Credentials without keys

The target looks for credentials in this order:

| Under `[deploy]` | How | Who decides what the app may do |
|---|---|---|
| `proxy_url` + `app` — or the organization's proxy url set on the platform (Plugins → AWS Lambda), which every deploy job carries as `AP_AWS_LAMBDA_PROXY_URL` with `AP_APP` | the deploy proxy in your account exchanges a platform token for the app's deploy-role credentials | the proxy's grants, in your account |
| `role_arn` | `sts assume-role-with-web-identity` with a platform token | the role's trust and permission policies |
| neither | the AWS CLI's own chain: SSO, profile, instance role | whatever that identity may do |

### The deploy proxy (recommended)

`proxy/` is a small SAM application you install **once per AWS account**. It decides who may deploy which app and hands out 15–60 minute credentials of that app's deploy role. It holds no platform secret — it verifies the platform's OIDC tokens against `https://<platform>/.well-known/jwks.json` — and the platform holds no AWS key.

**1. Install the proxy** — with your own AWS credentials, this one time (`aws sso login` or a profile; not the root account):

```bash
git clone https://github.com/actionplatform/apx-aws-lambda
cd apx-aws-lambda
./proxy/deploy.sh https://platform.example.com acme          # issuer url, organization slug [region] [stack name]
```

The script runs `sam build` + `sam deploy`, prints the stack's `ProxyUrl` and calls `/health`.

**2. Register the app** — happens by itself on the first deploy from the platform by someone with `org.manage`: the proxy answers 404 for an app it does not know, the plugin creates it (the deploy token carries `org.manage`) and goes on. Anyone else gets a readable refusal until a manager deploys once. From a machine logged in to the platform (`action-platform login`) as someone with `org.manage`, or to grant other subjects:

```bash
P=https://xxxx.lambda-url.us-east-1.on.aws

action-platform aws-lambda proxy create $P acme/shop/orders --region us-east-1
action-platform aws-lambda proxy grant  $P acme/shop/orders org:acme:project:shop:app:orders org:acme
action-platform aws-lambda proxy show   $P acme/shop/orders
```

`create` makes the app's two roles and grants deploy to the app itself; `grant` replaces the list of subject prefixes that may deploy — `org:acme` means anyone in the organization, `org:acme:project:shop` any app of the project, the full subject only deploys the platform runs for that app.

**3. Tell the platform (or the repository) where it is.** On the hosted platform, **Plugins** → **AWS Lambda** → **Configure** keeps the proxy url for the whole organization; every deploy job then carries it, together with the app's `org/project/app`, so the repository needs nothing beyond `target = "aws/lambda"`. From a machine, or to override per repository:

```toml
[deploy]
target = "aws/lambda"
proxy_url = "https://xxxx.lambda-url.us-east-1.on.aws"
app = "acme/shop/orders"
```

Through the proxy the stack is named `ap-acme-shop-orders-dev` / `-prod` — the deploy role reaches that prefix only, so the target sets `--stack-name` itself and `stack_name` in `samconfig.toml` is ignored; the repository never has to know the organization.

**4.** `action-platform deploy`.

### A role of your own (OIDC)

Without the proxy, register the platform as an identity provider once per account and write the trust policy yourself:

```bash
aws iam create-open-id-connect-provider --url https://platform.example.com --client-id-list sts.amazonaws.com
```

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::123456789012:oidc-provider/platform.example.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "platform.example.com:aud": "sts.amazonaws.com" },
      "StringLike": { "platform.example.com:sub": "org:acme:project:shop:app:orders" }
    }
  }]
}
```

`requirements/policy.json` from the overlay is a starting permission policy. Then:

```toml
[deploy]
target = "aws/lambda"
role_arn = "arn:aws:iam::123456789012:role/shop-deploy"
```

`sub` is `org:<org>:project:<project>:app:<app>` for a deploy the platform runs; `org:<org>` plus an `actor` claim for `action-platform deploy` from a logged-in machine (`StringLike` with `org:acme:*` covers both).

### The AWS CLI's own credentials

With neither `proxy_url` nor `role_arn`, `aws` and `sam` use what the machine has: `aws sso login`, a profile, an instance role. Still no key in a file when you use SSO — but nothing ties the identity to one app.

## Where the deploy runs

Independent of how credentials are obtained:

| Where | Token for the proxy / role comes from |
|---|---|
| the platform's worker (a deploy job) | signed by the platform for that app: `sub = org:…:project:…:app:…`, no scopes |
| a logged-in machine (`action-platform deploy`) | `POST /api/v1/identity/token` with the user's login: `sub = org:<org>`, `actor`, `scopes` |
| the overlay's `.github/workflows/deploy.yml` | GitHub's own OIDC — optional, unrelated to the proxy; delete the workflow when the platform deploys |

## The proxy in detail

### What the stack creates

| Resource | Purpose |
|---|---|
| Lambda + Function URL | the API (`python3.13`, standard library only — no compiled dependency, so `sam build` needs no pip) |
| DynamoDB table | one row per app: region, grants |
| `ActionPlatformBoundary` managed policy | the cap on every execution role the proxy creates |
| the function's role | may assume `role/action-platform/ap-deploy-*`, manage roles under `/action-platform/`, and create `ap-exec-*` roles only with the boundary attached |

Parameters: `IssuerUrl` (the platform's public url), `Organization` (one proxy serves one organization), `Version`.

### What it creates per app

| Role | Trust | Policy |
|---|---|---|
| `ap-deploy-<org>-<project>-<app>` | the proxy's function role | CloudFormation on stacks `ap-<org>-<project>-<app>*`; Lambda, API Gateway and logs with the same prefix; `iam:PassRole` on the execution role; SAM's managed bucket |
| `ap-exec-<org>-<project>-<app>` | `lambda.amazonaws.com` | `AWSLambdaBasicExecutionRole` + the boundary |

Both live under `/action-platform/`, carry tags `action-platform:app` and `action-platform:prefix`, and are idempotent: `create` again syncs trust policies, tags and policies without duplicating anything. `delete` removes both and the grants.

### The boundary

`ActionPlatformBoundary` is one policy for every app, scoped per app by the role's `action-platform:prefix` tag used as a policy variable in the resources:

- logs: `/aws/lambda/<prefix>*`
- S3 buckets, DynamoDB tables, SQS queues, SNS topics, Lambda functions: `<prefix>-*`
- Secrets Manager secrets and SSM parameters: `<prefix>/*`
- X-Ray tracing

An app's own `template.yaml` still declares what its function needs; the boundary caps it. Edit the boundary in `proxy/template.yaml` (and redeploy) to widen or narrow it for the whole account.

### The API

```
GET    /health                                   version, issuer, organization, boundary, account
POST   /apps/{org}/{project}/{app}               create both roles; body {"region": "…"}      org.manage
GET    /apps/{org}/{project}/{app}               roles and grants                             org.manage
DELETE /apps/{org}/{project}/{app}               delete roles and grants                      org.manage
PUT    /apps/{org}/{project}/{app}/grants        {"subjects": ["org:acme", …]}               org.manage
POST   /apps/{org}/{project}/{app}/credentials   {"duration": 900..3600} → temporary credentials   a grant
```

Every call except `/health` carries `Authorization: Bearer <platform token>` with `aud` = the proxy url. The proxy checks the signature against the issuer's JWKS, `iss`, `aud`, expiry, and that `organization` is the one it serves. Admin calls need `org.manage` in the token's `scopes`; deploy-job tokens carry no scopes, so a compromised worker cannot widen a grant. `credentials` needs the token's `sub` to start with one of the app's granted subject prefixes.

Errors are `{"error": "…"}`: 400 (input), 401 (token), 403 (organization, scope or grant), 404 (app not created).

The response of `credentials` also carries `stack_prefix` and `execution_role`; the target passes the latter as the overlay's `ExecutionRoleArn` parameter so `sam deploy` needs no `iam:CreateRole`.

### Upgrading

`/health` reports the proxy version; the plugin refuses a proxy older than the `MIN_PROXY` it was built for. Run `proxy/deploy.sh` again from a newer checkout — CloudFormation updates the stack in place, grants stay.

## Tools and commands

| | |
|---|---|
| `aws_lambda_stacks` / `action-platform aws-lambda stacks [prefix]` | CloudFormation stacks |
| `aws_lambda_functions` / `action-platform aws-lambda functions [prefix]` | Lambda functions |
| `action-platform aws-lambda proxy health <url>` | what the proxy serves |
| `action-platform aws-lambda proxy create <url> <org/project/app> [--region]` | the app's roles (org.manage) |
| `action-platform aws-lambda proxy show <url> <org/project/app>` | roles and grants |
| `action-platform aws-lambda proxy grant <url> <org/project/app> <subject>…` | who may deploy (org.manage) |
| `action-platform aws-lambda proxy delete <url> <org/project/app>` | remove the app from AWS IAM (org.manage) |

The `proxy` commands take the token from the CLI's login (`action-platform login`).

## Requirements

- `aws` and `sam` — on PATH when present; otherwise the `awscli` and `aws-sam-cli` packages the plugin depends on run as `python -m`, which is how the hosted platform deploys without the CLIs in its image.
- `AWS_REGION` or `region` under `[deploy]`.
- Network: `*.amazonaws.com`, and the proxy url when set.
- Installing the proxy: `sam`, `aws`, credentials for the account, Python 3.13 on PATH for `sam build` (or `sam build --use-container`).

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests fake `aws`, `sam`, IAM, STS and DynamoDB; nothing reaches AWS. `proxy/tests/test_oidc.py` signs a token with `cryptography` and verifies it with the proxy's standard-library RS256.
