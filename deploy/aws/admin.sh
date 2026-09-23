#!/usr/bin/env bash
# Run the admin CLI inside the running task, over ECS Exec (no SSH, no public
# admin port). Arguments pass straight to `python -m tradefloor.serve.hosted.admin`.
#
#   AWS_REGION=eu-west-2 deploy/aws/admin.sh create-key acme --plan trial
#   AWS_REGION=eu-west-2 deploy/aws/admin.sh usage
#   AWS_REGION=eu-west-2 deploy/aws/admin.sh revoke-all acme
#
# The session is recorded by ECS Exec in CloudTrail. A key printed by
# create-key appears in this terminal only; hand it over through a channel
# that does not keep a copy (not a ticket or chat history).
set -euo pipefail
: "${AWS_REGION:?set AWS_REGION}"
CLUSTER="${CLUSTER:-tradefloor-hosted}"
SERVICE="${SERVICE:-tradefloor-hosted}"

TASK="$(aws ecs list-tasks --region "$AWS_REGION" --cluster "$CLUSTER" --service-name "$SERVICE" \
  --desired-status RUNNING --query 'taskArns[0]' --output text)"
if [[ -z "$TASK" || "$TASK" == "None" ]]; then
  echo "no running task in $CLUSTER/$SERVICE" >&2
  exit 1
fi
CMD="python -m tradefloor.serve.hosted.admin"
for a in "$@"; do CMD+=" $(printf '%q' "$a")"; done
exec aws ecs execute-command --region "$AWS_REGION" --cluster "$CLUSTER" --task "$TASK" \
  --container tradefloor --interactive --command "$CMD"
