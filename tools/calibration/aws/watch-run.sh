#!/bin/bash
# Watch one AWS calibration run: instance state, CloudWatch CPU, S3 markers.
#
#   bash tools/calibration/aws/watch-run.sh <run-name> <instance-id> [minutes]
#
# Prints one line whenever anything changes, and exits when the run finishes
# or the instance goes away. Written as a FILE rather than inline because an
# inline poll loop is shell that cannot be statically analysed, so every
# invocation prompts for permission; this takes two plain arguments.
#
# CPU comes from CloudWatch on purpose. §13 and the runbook: a terminated
# instance still leaves a DONE marker in S3, and a hung job looks identical
# to a running one from the bucket, so the marker alone is not evidence that
# work happened.
set -u

RUN="${1:?usage: watch-run.sh <run-name> <instance-id> [minutes]}"
INSTANCE="${2:?usage: watch-run.sh <run-name> <instance-id> [minutes]}"
MINUTES="${3:-45}"

P=(--profile fixtures-admin --region us-east-2)
BUCKET="s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/$RUN"

prev=""
for _ in $(seq 1 "$MINUTES"); do
  state=$(aws ec2 describe-instances "${P[@]}" --instance-ids "$INSTANCE" \
            --query 'Reservations[0].Instances[0].State.Name' \
            --output text 2>/dev/null) || state=unknown
  [ -z "$state" ] && state=unknown

  marks=$(aws s3 ls "$BUCKET/" "${P[@]}" 2>/dev/null | awk '{print $4}' | tr '\n' ',')

  cpu=$(aws cloudwatch get-metric-statistics "${P[@]}" \
          --namespace AWS/EC2 --metric-name CPUUtilization \
          --dimensions "Name=InstanceId,Value=$INSTANCE" \
          --start-time "$(date -u -v-15M +%Y-%m-%dT%H:%M:%SZ)" \
          --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
          --period 300 --statistics Average \
          --query 'sort_by(Datapoints,&Timestamp)[-1].Average' \
          --output text 2>/dev/null)
  [ -z "$cpu" ] || [ "$cpu" = "None" ] && cpu="-"

  line="$RUN state=$state cpu=$cpu marks=${marks:-none}"
  if [ "$line" != "$prev" ]; then
    echo "$line"
    prev="$line"
  fi

  case "$marks" in
    *DONE*) echo "$RUN FINISHED"; exit 0 ;;
  esac
  case "$state" in
    terminated|shutting-down) echo "$RUN INSTANCE $state"; exit 0 ;;
  esac
  sleep 60
done
echo "$RUN still running after $MINUTES minutes"
