#!/usr/bin/env bash
# Build, push and deploy the hosted server to AWS. NOT RUN YET.
#
# This script creates billable, internet-facing resources. It refuses to run
# unless TRADEFLOOR_DEPLOY_CONFIRM is set to the phrase below, which exists so
# that nobody (and no agent) runs it by accident before the owner has said go
# and made the decisions in docs/serve/HOSTED.md ("Before launch").
#
#   TRADEFLOOR_DEPLOY_CONFIRM=owner-approved-public-launch \
#   AWS_REGION=eu-west-2 DOMAIN=api.example.com HOSTED_ZONE_ID=Z0123... \
#   ALERT_EMAIL=ops@example.com TAG=0.1.0 deploy/aws/deploy.sh
#
# Needs: aws CLI v2 with credentials for the target account, docker with
# buildx (for linux/arm64), and the session-manager plugin for ECS Exec.
set -euo pipefail

if [[ "${TRADEFLOOR_DEPLOY_CONFIRM:-}" != "owner-approved-public-launch" ]]; then
  echo "refusing: this deploys a public service and costs money." >&2
  echo "Set TRADEFLOOR_DEPLOY_CONFIRM=owner-approved-public-launch once the owner has approved." >&2
  exit 2
fi

: "${AWS_REGION:?set AWS_REGION}"
: "${DOMAIN:?set DOMAIN (the API hostname)}"
: "${TAG:?set TAG (an immutable image tag, e.g. the release version)}"
STACK="${STACK:-tradefloor-hosted}"
REPO="${REPO:-tradefloor-hosted}"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-}"
CERTIFICATE_ARN="${CERTIFICATE_ARN:-}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE="$REGISTRY/$REPO:$TAG"

echo "== 1. ECR repository ($REPO)"
aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$REPO" >/dev/null 2>&1 ||
  aws ecr create-repository --region "$AWS_REGION" --repository-name "$REPO" \
    --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE >/dev/null

echo "== 2. build and push $IMAGE (linux/arm64)"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker buildx build --platform linux/arm64 -f "$ROOT/deploy/Dockerfile" -t "$IMAGE" --push "$ROOT"

echo "== 3. stack $STACK"
aws cloudformation deploy --region "$AWS_REGION" --stack-name "$STACK" \
  --template-file "$HERE/cloudformation.yml" --capabilities CAPABILITY_IAM \
  --parameter-overrides ImageUri="$IMAGE" DomainName="$DOMAIN" \
    HostedZoneId="$HOSTED_ZONE_ID" CertificateArn="$CERTIFICATE_ARN" AlertEmail="$ALERT_EMAIL"

aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK" \
  --query 'Stacks[0].Outputs' --output table

cat <<EOF

Deployed. Next:
  - wait for the service to be stable:
      aws ecs wait services-stable --region $AWS_REGION --cluster tradefloor-hosted --services tradefloor-hosted
  - check https://$DOMAIN/healthz
  - make the first key (shown once):
      deploy/aws/admin.sh create-key <owner> --plan trial
EOF
