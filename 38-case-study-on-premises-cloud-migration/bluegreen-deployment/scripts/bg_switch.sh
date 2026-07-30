#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 38, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2006,SC2086,SC2317

# Blue-Green deployment switcher via AWS ALB listener modification
# Tracks current active colour in S3 for state management
# Used for zero-downtime releases in regulated iGaming environments

set -ex

B_TARGETGROUPARN=$1
G_TARGETGROUPARN=$2
ALB_LISTENER=$3
S3_BG_BUCKETNAME=$4
AWS_PROFILE=$5
AWS_REGION=$6

aws s3 cp s3://$S3_BG_BUCKETNAME/current /tmp/colour --profile=$AWS_PROFILE

colour=`cat /tmp/colour`

if [ "$colour" == "blue" ]; then
  echo "env is currently blue, changing to green"
  echo -n "green" | aws s3 cp - s3://$S3_BG_BUCKETNAME/current --acl private --content-type "text/plain" --profile=$AWS_PROFILE
  aws elbv2  modify-listener --listener-arn $ALB_LISTENER --default-actions Type=forward,TargetGroupArn=$B_TARGETGROUPARN,Order=1 --region=$AWS_REGION --profile=$AWS_PROFILE
  echo "env is now green"
elif [ "$colour" == "green" ]; then
  echo "env is currently green, changing to blue"
  echo -n "blue"  | aws s3 cp - s3://$S3_BG_BUCKETNAME/current --acl private --content-type "text/plain" --profile=$AWS_PROFILE
  aws elbv2  modify-listener --listener-arn $ALB_LISTENER --default-actions Type=forward,TargetGroupArn=$G_TARGETGROUPARN,Order=1 --region=$AWS_REGION --profile=$AWS_PROFILE
  echo "env is now blue"
else
  exit 1
  echo "the state on the s3 isnt blue or green, something is quite wrong"
fi
