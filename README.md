# apx-aws-lambda

AWS Lambda for [Action Platform](https://github.com/actionplatform/action-platform): the `aws/lambda` deploy target (SAM), its overlay, read-only tools and commands, and an IAM-only **connect stack** for your AWS account so that no AWS key ever lives on the platform, on a machine or in a repository — and nothing of ours runs in the account. `apx-` is the prefix every Action Platform extension carries.

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
  - [A connected account (recommended)](#a-connected-account-recommended)
  - [A role of your own (OIDC)](#a-role-of-your-own-oidc)
  - [The AWS CLI's own credentials](#the-aws-clis-own-credentials)
- [Where the deploy runs](#where-the-deploy-runs)
- [Tools and commands](#tools-and-commands)
- [Requirements](#requirements)
- [Development](#development)

## What the plugin does

| | |
|---|---|
| Overlay | `template.yaml`, `samconfig.toml`, a one-line `Makefile` (`ap-build package`), `requirements/` (IAM examples), `.github/workflows/deploy.yml` (optional). One template for every language: the Lambda Web Adapter layer runs the project as an HTTP server on `$PORT` — `run.sh` from `ap-build` (Python `python3.12`, Node `nodejs22.x`, Java/Kotlin `java21`, Ruby `ruby3.3`) or the static `bootstrap` on `provided.al2023` (Go). Every `web/*` template of the official repository deploys; the health route is `/health` (API Gateway keeps `/ping` for itself on `execute-api`) |
| Readiness | before a deploy, without building or changing anything: the tooling is there, the overlay is applied, the stack name resolves, the credentials work, the stack is not mid-operation or failed (a `ROLLBACK_COMPLETE` first creation is a warning — the deploy deletes it), the deploy role may do what the template needs (simulated with `iam:SimulatePrincipalPolicy` against the stack, the function, its log group, the execution role and every public layer the template references — the Lambda Web Adapter's, for one) and `sam validate --lint` passes. The platform runs it after every release for `dev` and `prod` and refuses a blocked deploy unless forced; `action-platform readiness` runs it on a machine |
| Deploy | `sam build` + `sam deploy --config-env <stage>` (`dev` → `default`, `prod` → `prod`); a stack left in `ROLLBACK_COMPLETE` by a failed first creation is deleted first. Every line `sam` prints reaches the platform's run log as it appears — the deploy form, the run dialog, `action-platform logs -f` |
| Rollback | CloudFormation `rollback-stack` — previous stack state |
| Diagnose | stack status and the HTTP API url |
| Destroy | `sam delete` of the stage's stack, the execution role SAM created with it |
| Tools | `aws_lambda_stacks`, `aws_lambda_functions` |
| Commands | `action-platform aws-lambda connect\|stacks\|functions` |

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
| `role_arn` | `sts assume-role-with-web-identity` with a platform token | the role's trust and permission policies |
| neither, and the organization's deploy role set on the platform (Plugins → AWS Lambda), which every deploy job carries as `AP_AWS_LAMBDA_ROLE_ARN` with `AP_APP` | `sts assume-role-with-web-identity` on the connect stack's role with the platform's token for the app | the token's session tag and the role's policy, in your account |
| neither | the AWS CLI's own chain: SSO, profile, instance role | whatever that identity may do |

### A connected account (recommended)

One CloudFormation stack, IAM only — no function, no table, nothing to upgrade. AWS itself trusts the platform's OIDC tokens; the token's session tag keeps each deploy inside its app.

**1. Create the stack** — once per AWS account, with your own AWS credentials (not the root account). Either in the console: CloudFormation → Create stack → *Upload a template file* → [`apx_aws_lambda/connect/template.yaml`](apx_aws_lambda/connect/template.yaml), parameters `IssuerUrl` (the platform's public url) and `Organization` (its slug), acknowledge IAM; or from a machine:

```bash
action-platform aws-lambda connect https://platform.example.com acme      # --region, --stack-name, --oidc-provider-arn
```

**2. Tell the platform** — Plugins → AWS Lambda → Configure → **Deploy role ARN** = the stack's `DeployRoleArn` output. Every app of the organization whose `platform.toml` says `target = "aws/lambda"` deploys from then on; nothing to register per app.

What the stack creates:

| Resource | Purpose |
|---|---|
| IAM OIDC provider | trusts `IssuerUrl` for audience `sts.amazonaws.com`; pass `OidcProviderArn` when the account already has one for the platform |
| `ActionPlatformAppBoundary` (`/action-platform/`) | the cap on every execution role an app's stack creates — logs `/aws/lambda/<prefix>*`; S3 buckets, DynamoDB tables, SQS queues, SNS topics, Lambda functions `<prefix>-*`; Secrets Manager secrets and SSM parameters `<prefix>/*`; X-Ray — `<prefix>` being the role's `action-platform:prefix` tag. An app's own `template.yaml` still declares what its function needs; the boundary caps it |
| `ActionPlatformDeploy` role (`/action-platform/`) | trust: `AssumeRoleWithWebIdentity` for `sub` `org:<org>:*`, and `TagSession` only for the one tag `action-platform:prefix` with a value `ap-<org>-*`. Policy: every resource under `${aws:PrincipalTag/action-platform:prefix}` — CloudFormation stacks, Lambda, logs; API Gateway; the public Lambda Web Adapter layers; SAM's bucket; `iam:CreateRole` on `<prefix>-*` only with the boundary attached and the app's own prefix tag, `iam:PassRole` of those roles to Lambda only; `iam:SimulatePrincipalPolicy` on itself |

How a deploy runs: the platform signs a token for the app with the claim `https://aws.amazon.com/tags` → `principal_tags.action-platform:prefix = ap-<org>-<project>-<app>`; the target assumes the deploy role with it, names the stack `ap-<org>-<project>-<app>-dev|prod`, passes `PermissionsBoundaryArn` to the overlay's template and tags the stack with the prefix, so the execution role SAM creates carries both the boundary and the tag. `sam delete` removes that role with the stack. Who may deploy which app is the platform's decision — it only signs a token for an app the job or the caller is for.

Readiness simulates the deploy role with the session tag and the boundary as context entries, and flags an overlay without the `PermissionsBoundaryArn` parameter (apply the overlay again). The stack's policy is the one place to widen or narrow what deploys may do: edit and update the stack.

### A role of your own (OIDC)

Instead of the connect stack, register the platform as an identity provider once per account and write the trust policy yourself:

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

With neither `role_arn` nor a connected account, `aws` and `sam` use what the machine has: `aws sso login`, a profile, an instance role. Still no key in a file when you use SSO — but nothing ties the identity to one app.

## Where the deploy runs

Independent of how credentials are obtained:

| Where | Token for the role comes from |
|---|---|
| the platform's worker (a deploy job) | signed by the platform for that app: `sub = org:…:project:…:app:…`, the `action-platform:prefix` session tag |
| a logged-in machine (`action-platform deploy`) | `POST /api/v1/identity/token` with the user's login: `sub = org:<org>`, `actor`, `scopes` |
| the overlay's `.github/workflows/deploy.yml` | GitHub's own OIDC — optional, unrelated to the platform; delete the workflow when the platform deploys |

## Tools and commands

| | |
|---|---|
| `aws_lambda_stacks` / `action-platform aws-lambda stacks [prefix]` | CloudFormation stacks |
| `aws_lambda_functions` / `action-platform aws-lambda functions [prefix]` | Lambda functions |
| `action-platform aws-lambda connect <platform url> <organization>` | the connect stack, in the account the CLI is logged in to |

## Requirements

- `aws` and `sam` — on PATH when present; otherwise the `awscli` and `aws-sam-cli` packages the plugin depends on run as `python -m`, which is how the hosted platform deploys without the CLIs in its image.
- `AWS_REGION` or `region` under `[deploy]`.
- Network: `*.amazonaws.com`.
- Connecting the account: credentials for it, once — the console, or `aws` for `action-platform aws-lambda connect`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests fake `aws` and `sam`; nothing reaches AWS.
