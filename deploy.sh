#!/usr/bin/env bash
# Deploy the Isaias hourly capture to Amazon ECS (Fargate) in the BIL6 account (us-east-2).
# Everything is separate from the TWL cross-validation deployment and named bil6-isaias-camera-capture*.
#
# Creates: IAM roles <N>-exec and <N>-task, log group /ecs/<N>, security group <N>-sg (no inbound rules),
#          ECS cluster <N>-cluster, task definition <N>, and ECS service <N> (1 task, 0.25 vCPU / 0.5 GB).
# The S3 bucket <N>-858933856877 already exists (code, frames, Windy key).
# The task uses the public python:3.12-slim image and downloads the code from the bucket at start-up,
# so no image build or public repository is needed.
#
# Cost: about $0.02/hour (≈ $1.50 for the three days) plus a few cents of S3.
# The task sets its own service to 0 tasks after Sun Oct 11 24:00 CDT.
#
# Usage:  ./deploy.sh all        create everything and start the service
#         ./deploy.sh code       upload new code and restart the task
#         ./deploy.sh teardown   remove ECS, IAM and network resources (frames stay in S3)
set -euo pipefail
export AWS_PAGER="" AWS_DEFAULT_REGION=us-east-2
N=bil6-isaias-camera-capture
ACCT=858933856877
REG=us-east-2
B=$N-$ACCT
HERE="$(cd "$(dirname "$0")" && pwd)"

upload_code() {
  local zip; zip="$(mktemp -d)/code.zip"
  (cd "$HERE" && zip -q -r "$zip" isaias_capture.py requirements.txt data)
  aws s3 cp "$zip" "s3://$B/code/isaias_capture.zip" --sse AES256 --only-show-errors
  echo "code uploaded to s3://$B/code/isaias_capture.zip"
}

case "${1:-}" in
code)
  upload_code
  aws ecs update-service --cluster $N-cluster --service $N --force-new-deployment >/dev/null
  echo "service restarted with the new code"
  ;;
teardown)
  aws ecs update-service --cluster $N-cluster --service $N --desired-count 0 >/dev/null || true
  aws ecs delete-service --cluster $N-cluster --service $N --force >/dev/null || true
  sleep 30
  aws ecs delete-cluster --cluster $N-cluster >/dev/null || true
  for arn in $(aws ecs list-task-definitions --family-prefix $N --query 'taskDefinitionArns' --output text); do
    aws ecs deregister-task-definition --task-definition "$arn" >/dev/null; done
  aws iam delete-role-policy --role-name $N-task --policy-name $N-access || true
  aws iam delete-role --role-name $N-task || true
  aws iam detach-role-policy --role-name $N-exec --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy || true
  aws iam delete-role --role-name $N-exec || true
  aws ec2 delete-security-group --group-id "$(aws ec2 describe-security-groups --filters Name=group-name,Values=$N-sg --query 'SecurityGroups[0].GroupId' --output text)" || true
  echo "ECS, IAM and network resources removed. Frames stay in s3://$B (delete with: aws s3 rb s3://$B --force)."
  ;;
all)
  upload_code
  TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  aws iam create-role --role-name $N-exec --assume-role-policy-document "$TRUST" --tags Key=Project,Value=BIL6 >/dev/null
  aws iam attach-role-policy --role-name $N-exec --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  aws iam create-role --role-name $N-task --assume-role-policy-document "$TRUST" --tags Key=Project,Value=BIL6 >/dev/null
  # Only its own bucket, and only setting its own service to 0 tasks when the period is over.
  aws iam put-role-policy --role-name $N-task --policy-name $N-access --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {\"Effect\": \"Allow\", \"Action\": [\"s3:GetObject\", \"s3:PutObject\"], \"Resource\": \"arn:aws:s3:::$B/*\"},
      {\"Effect\": \"Allow\", \"Action\": \"s3:ListBucket\", \"Resource\": \"arn:aws:s3:::$B\"},
      {\"Effect\": \"Allow\", \"Action\": \"ecs:UpdateService\", \"Resource\": \"arn:aws:ecs:$REG:$ACCT:service/$N-cluster/$N\"}]}"
  aws logs create-log-group --log-group-name /ecs/$N --tags Project=BIL6
  aws logs put-retention-policy --log-group-name /ecs/$N --retention-in-days 30
  VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  SG=$(aws ec2 create-security-group --group-name $N-sg --description "outbound only" --vpc-id "$VPC" \
       --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=BIL6},{Key=Name,Value=$N}]" \
       --query GroupId --output text)
  aws ecs create-cluster --cluster-name $N-cluster --capacity-providers FARGATE --tags key=Project,value=BIL6 >/dev/null
  sleep 15                                     # new IAM roles take a few seconds before ECS can use them
  BOOT="pip install --no-cache-dir -q requests==2.32.3 boto3==1.35.36 tzdata==2024.2 && python -c \"import boto3,zipfile; boto3.client('s3').download_file('$B','code/isaias_capture.zip','/tmp/c.zip'); zipfile.ZipFile('/tmp/c.zip').extractall('/app')\" && cd /app && exec python -u isaias_capture.py run"
  TD="$(mktemp -d)/taskdef.json"
  python3 - "$N" "$ACCT" "$B" "$BOOT" > "$TD" <<'PY'
import json, sys
n, acct, b, boot = sys.argv[1:]
print(json.dumps({
  "family": n, "requiresCompatibilities": ["FARGATE"], "networkMode": "awsvpc", "cpu": "256", "memory": "512",
  "executionRoleArn": f"arn:aws:iam::{acct}:role/{n}-exec", "taskRoleArn": f"arn:aws:iam::{acct}:role/{n}-task",
  "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
  "containerDefinitions": [{
    "name": "capture", "image": "public.ecr.aws/docker/library/python:3.12-slim", "essential": True,
    "entryPoint": ["bash", "-c"], "command": [boot],
    "environment": [{"name": "S3_BUCKET", "value": b}, {"name": "AWS_DEFAULT_REGION", "value": "us-east-2"},
                    {"name": "ECS_CLUSTER", "value": f"{n}-cluster"}, {"name": "ECS_SERVICE", "value": n}],
    "logConfiguration": {"logDriver": "awslogs", "options": {
      "awslogs-group": f"/ecs/{n}", "awslogs-region": "us-east-2", "awslogs-stream-prefix": "isaias"}}}],
  "tags": [{"key": "Project", "value": "BIL6"}]}))
PY
  aws ecs register-task-definition --cli-input-json "file://$TD" >/dev/null
  SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=default-for-az,Values=true --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
  aws ecs create-service --cluster $N-cluster --service-name $N --task-definition $N --desired-count 1 --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --propagate-tags SERVICE --enable-ecs-managed-tags --tags key=Project,value=BIL6 >/dev/null
  echo "Service $N created. Watch it: aws logs tail /ecs/$N --follow"
  ;;
*) echo "usage: $0 all|code|teardown"; exit 1 ;;
esac
