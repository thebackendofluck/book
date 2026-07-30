# Blue-Green Deployment via ALB Listener Switching

Production-derived blue-green deployment script that achieves zero-downtime
releases by switching AWS ALB listener rules between two target groups.
The current active colour (blue or green) is tracked in an S3 file.

## How It Works

1. Read current colour from `s3://<bucket>/current`
2. If currently **blue**: switch ALB listener to forward to the **blue** target
   group (which has the new code), mark state as **green**
3. If currently **green**: switch ALB listener to forward to the **green** target
   group (which has the new code), mark state as **blue**
4. The inactive colour always has the previous version, enabling instant rollback
   by re-running the script

## Usage

```bash
./scripts/bg_switch.sh \
  <blue-target-group-arn> \
  <green-target-group-arn> \
  <alb-listener-arn> \
  <s3-bucket-name> \
  <aws-profile> \
  <aws-region>
```

## Rollback

To roll back, simply run the script again -- it will switch back to the
previous colour which still has the old version deployed.

## Source

Adapted from the production deployment pipeline used for US-regulated
iGaming platform releases across multiple state jurisdictions.
