# Changelog

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
