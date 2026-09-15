#!/usr/bin/env bash
# Deploys the Action Platform deploy proxy into the AWS account the CLI is logged in to.
#
#   ./deploy.sh <issuer-url> <organization> [region] [stack-name]
#
#   issuer-url    the platform's public url (AP_PUBLIC_URL), e.g. https://platform.example.com
#   organization  the organization slug on the platform
#   region        default us-east-1
#   stack-name    default action-platform-proxy
#
# Needs: sam, aws, and AWS credentials for the account (aws sso login / a profile) — this one time.
set -euo pipefail

ISSUER_URL="${1:?issuer url, e.g. https://platform.example.com}"
ORGANIZATION="${2:?organization slug}"
REGION="${3:-us-east-1}"
STACK="${4:-action-platform-proxy}"
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(cat "$HERE/../LAST_VERSION" 2>/dev/null || echo 0.1.0)"

cd "$HERE"

echo "==> account"
aws sts get-caller-identity --region "$REGION" --output table

echo "==> sam build"
sam build

echo "==> sam deploy $STACK ($REGION)"
sam deploy \
  --stack-name "$STACK" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "IssuerUrl=${ISSUER_URL%/}" \
    "Organization=$ORGANIZATION" \
    "Version=$VERSION" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

PROXY_URL="$(aws cloudformation describe-stacks \
  --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ProxyUrl'].OutputValue" --output text)"
PROXY_URL="${PROXY_URL%/}"

echo "==> health"
curl -fsS "$PROXY_URL/health"
echo

cat <<MSG

Proxy is up: $PROXY_URL

In each app's platform.toml:

[deploy]
target = "aws/lambda"
proxy_url = "$PROXY_URL"
app = "$ORGANIZATION/<project>/<app>"

Create the app's roles and grant deploy (token with org.manage, audience $PROXY_URL):

  TOKEN=\$(curl -s -X POST ${ISSUER_URL%/}/api/v1/identity/token \\
    -H "Authorization: Bearer <platform token>" -H "content-type: application/json" \\
    -d '{"audience":"$PROXY_URL"}' | jq -r .token)
  curl -X POST -H "Authorization: Bearer \$TOKEN" $PROXY_URL/apps/$ORGANIZATION/<project>/<app>
  curl -X PUT  -H "Authorization: Bearer \$TOKEN" $PROXY_URL/apps/$ORGANIZATION/<project>/<app>/grants \\
    -H "content-type: application/json" -d '{"subjects":["org:$ORGANIZATION"]}'

stack_name in samconfig.toml must start with ap-$ORGANIZATION-<project>-<app>.
MSG
