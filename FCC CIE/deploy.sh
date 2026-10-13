#!/usr/bin/env bash
set -eu

: "${FRONTEND_BUCKET:?Set FRONTEND_BUCKET to the S3 bucket name}"
: "${DISTRIBUTION_ID:?Set DISTRIBUTION_ID to the CloudFront distribution ID}"

aws s3 sync frontend/ "s3://${FRONTEND_BUCKET}" --delete
aws cloudfront create-invalidation \
  --distribution-id "${DISTRIBUTION_ID}" \
  --paths "/*"
