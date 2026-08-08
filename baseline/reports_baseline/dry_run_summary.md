# Dry-Run Summary

## Totals by AWS service

- ALB: 12
- ECS: 20
- NLB: 2
- RDS: 12
- RabbitMQ: 6
- Redis: 10

- **Total rules planned:** 62
- **Warning:** 30
- **Critical:** 32

## Resources skipped

None.

## Explicitly not created

- Duplicate API ALB rules monitoring the same ALB metrics
- Aurora FreeStorageSpace rules
- ECS rules using ClusterName=* or ServiceName=*
- CloudWatch capacity-provider scaling alarms
- Prometheus alerts
- Out-of-scope RDS, Redis, or RabbitMQ resources
- Legacy release-tagged resources
- Out-of-scope frontend clusters

## Duplicate checks

- Unique UIDs: 62 / 62
- Unique titles: 62 / 62

## Grafana prerequisites

- URL: https://grafana.example.com
- Folder UID: example-folder
- Datasource UID: example-cloudwatch
- Contact point: engineering-alerts (to be created before apply)
- Rule prefix: alerts-

## Deployment note

This dry-run file is generated before write operations. Apply proceeds only after backup, validation, notification stack, and canary succeed.
