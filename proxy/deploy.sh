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

On the platform: Plugins → AWS Lambda → Configure → Deploy proxy URL = $PROXY_URL
Then deploy any app whose platform.toml says target = "aws/lambda": the first deploy by an
organization manager registers the app here (its roles, granted to the app itself).

From a machine instead (token with org.manage, audience $PROXY_URL):

  action-platform aws-lambda proxy create $PROXY_URL $ORGANIZATION/<project>/<app>
  action-platform aws-lambda proxy grant  $PROXY_URL $ORGANIZATION/<project>/<app> org:$ORGANIZATION

Stacks are named ap-$ORGANIZATION-<project>-<app>-<dev|prod>; the target sets --stack-name itself.
MSG
