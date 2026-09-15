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

## Credentials without keys

Set `role_arn` and nothing else: the target assumes that role with a short-lived OIDC token the platform signs (`sts assume-role-with-web-identity`). No access key on the platform, on your machine or in the repository; what the app may do is the role's policy.

```toml
[deploy]
target = "aws/lambda"
role_arn = "arn:aws:iam::123456789012:role/shop-deploy"
```

Once per AWS account, register the platform as an identity provider — its discovery document is `https://<platform>/.well-known/openid-configuration`:

```bash
aws iam create-open-id-connect-provider --url https://platform.example.com --client-id-list sts.amazonaws.com
```

Then a role per app (or per project) whose trust policy names the platform and the app; `requirements/policy.json` from the overlay is its permission policy:

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

`sub` is `org:<org>:project:<project>:app:<app>` for a deploy the platform runs; `org:<org>` plus an `actor` claim for `action-platform deploy` from a logged-in machine (`StringLike` with `org:acme:*` covers both). Without `role_arn` the AWS CLI's own chain applies: `aws sso login`, a profile, an instance role — still no key in a file when you use SSO.

Tools (`action-platform mcp`): `aws_lambda_stacks`, `aws_lambda_functions`. Commands: `action-platform aws-lambda stacks|functions`. Deploying itself goes through the core's `deploy` / `rollback` / `diagnose`, which drive the target.

Needs: AWS CLI v2 (`aws`), SAM CLI (`sam`), `AWS_REGION` (or `region` under `[deploy]`), and either `role_arn` (OIDC, no keys) or the AWS CLI's own credentials. Talks to `*.amazonaws.com` only.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests fake `aws` and `sam`; nothing reaches AWS.
