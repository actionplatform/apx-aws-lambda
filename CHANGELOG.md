# Changelog

## v0.3.7 — 2026-09-16

### Features
- **target:** delete drops the stage's stack and, once no stage is left, the app on the proxy

### Docs
- destroy through the proxy

### Tests
- **target:** delete — other stage alive, last stage, no org.manage

## v0.3.6 — 2026-09-16

### Features
- **target:** the stack is named from the prefix the proxy granted — samconfig.toml's stack_name is not the organization's business

### Bug Fixes
- **proxy:** retry AssumeRole while IAM propagates a role just created — the first deploy of an app

### Docs
- stack name through the proxy
- **proxy:** deploy.sh points at Plugins → AWS Lambda; the first deploy registers the app

### Tests
- **target:** --stack-name follows the granted prefix
- **proxy:** credentials wait for the role

## v0.3.5 — 2026-09-15

### Features
- **proxy:** an app the proxy does not know is registered on the first deploy when the token carries org.manage

### Docs
- the first deploy registers the app

### Tests
- **proxy:** registration on the way, and the refusal when the token may not

## v0.3.4 — 2026-09-15

### Features
- **plugin:** declare proxy_url as an option and the name AWS Lambda — the platform draws the form

### Refactoring
- **shell:** no plugins volume to carry into a child — AP_PLUGINS_DIR is gone from the core

### Docs
- the form is on the card
- the proxy url is set under Plugins → AWS Lambda

### Tests
- **shell:** the child env

### Build
- action-platform>=0.17.9 (Plugin.options)

## v0.3.3 — 2026-09-15

### Features
- **target:** the proxy url and app come from the platform (ctx.env) when platform.toml does not name them

### Bug Fixes
- **overlay:** the layer builds from wheels for arm64/python3.12 on any host, without poetry
- **overlay:** cfn-lint ignores E3031 on the empty ExecutionRoleArn default; sam runs without the telemetry notice
- **proxy:** keep the boundary's description — changing it replaces the named policy and the update fails
- **proxy:** GetRole on a role that does not exist yet is checked without the path; AWS errors answer 500 with the reason

### Docs
- **readme:** organization-wide proxy url

### Tests
- **target:** proxy named by the platform
- **shell:** telemetry off in every child env

### Style
- **overlay:** tools formatted like the generated repository lints them

## v0.3.2 — 2026-09-15

### Bug Fixes
- **shell:** python -m awscli/samcli children see the plugins volume through PYTHONPATH

### Tests
- **shell:** module_env carries the plugins dir

## v0.3.1 — 2026-09-15

### Features
- **cli:** action-platform aws-lambda proxy health|create|show|grant|delete
- **overlay:** ExecutionRoleArn parameter for a role the proxy created
- **target:** proxy_url + app under [deploy] take credentials from the deploy proxy
- **proxy:** deploy proxy — a SAM app that grants deploy on an app and hands out its deploy-role credentials

### Bug Fixes
- **proxy:** UpdateAssumeRolePolicy for the second create; boundary scoped per app through the role's prefix tag
- **proxy:** verify RS256 with the standard library — no compiled dependency, no pip in sam build; runtime python3.13

### Docs
- **readme:** rewrite — credentials order, proxy install and API, boundary, where deploys run
- **readme:** boundary per app, idempotent roles, where deploys run
- **readme:** deploy.sh
- **readme:** the deploy proxy

### Tests
- **proxy:** prefix tag on the execution role
- **proxy:** RS256 verification against a signed token
- **proxy:** client, target and handler decisions

### CI
- **lint:** exclude overlays from ruff, the generated project lints them with its own import layout

### Chores
- **deps:** cryptography for the oidc tests
- **gitignore:** sam build output and the proxy's local samconfig
- **proxy:** deploy.sh — build, deploy, print the proxy url and the next steps
- **deps:** boto3 and PyJWT for the proxy tests; pytest testpaths

## v0.3.0 — 2026-09-15

### Features
- aws and sam run as python -m from the plugin's own dependencies when not on PATH; core 0.17

### Bug Fixes
- **overlay:** lambda_handler imports sorted the way ruff wants

### Build
- role_arn needs action-platform 0.17

## v0.2.0 — 2026-09-15

### Features
- role_arn assumes a role with the platform's OIDC token — no access keys

## v0.1.0 — 2026-09-15

### Features
- aws/lambda and aws/amplify deploy targets with overlays, aws.* tools, action-platform aws commands

### Refactoring
- apx-aws-lambda — only the aws/lambda target, tools as aws_lambda_<name>

### CI
- explicit ruff rule set
- publish to PyPI on release with trusted publishing; code quality on pull requests

### Style
- import order
