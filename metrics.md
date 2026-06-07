# MVCR Bot And Fetcher Metrics

## Goal

Expose first-class Prometheus metrics from the MVCR Telegram bot and fetcher processes so operational problems are visible through alerts before someone manually checks logs.

The desired setup is:

- Bot and fetcher expose native `/metrics` HTTP endpoints.
- Kubernetes exposes those endpoints through chart-managed metrics services or pod monitors.
- Prometheus discovers and scrapes the bot and fetcher metrics through `ServiceMonitor` or `PodMonitor` resources.
- Alert rules use stable Prometheus counters, gauges, and histograms to detect exhausted fetch failures, sustained retry storms, bot errors, and notification delivery failures.
- RabbitMQ queue metrics continue to cover infrastructure-level symptoms such as queue pileups and missing consumers.

The metrics should not expose user-identifying data. Do not use labels such as Telegram chat ID, username, application number, raw status text, or raw exception messages.

## Label Guidelines

Preferred low-cardinality labels:

- `component`: `bot` or `fetcher`
- `version`: application version
- `queue`: RabbitMQ queue name
- `request_type`: `fetch`, `refresh`, or `expire`
- `source`: `oam` or `zov`
- `result`: stable outcome such as `success`, `retry`, `failed`, `processed`, or `dropped`
- `stage`: stable processing stage such as `browser`, `rabbitmq`, `telegram`, `db`, `notification_dispatcher`, or `latency_check`
- `error_type`: stable error category, not the raw exception string
- `notification_kind`: `status_change`, `failed_fetch`, `force_refresh_unchanged`, or `expiration`
- `command`: admin/user command name, if command metrics are added
- `state`: request state such as `waiting` or `locked`

Avoid labels with unbounded or sensitive values.

## Fetcher Metrics

### `mvcr_fetcher_requests_total`

Counter for fetcher request outcomes.

Labels:

- `request_type`: `fetch` or `refresh`
- `source`: `oam` or `zov`
- `result`: `success`, `retry`, or `failed`

Semantics:

- `result="success"`: status was fetched and published to `StatusUpdateQueue`
- `result="retry"`: request failed this attempt and was requeued with `x-retry-count`
- `result="failed"`: request exhausted queue-level `MAX_RETRIES` and an error status was published

Use this for exhausted-failure and sustained retry-storm alerts. Do not count individual browser retries or normal queue requeues as hard failures.

### `mvcr_fetcher_errors_total`

Counter for fetcher-side operational errors.

Labels:

- `stage`: `browser`, `rabbitmq`, `latency_check`, `status_publish`, or another stable stage
- `error_type`: stable category such as `timeout`, `connection`, `unexpected`, `status_mismatch`, or `publish_failed`

Use this to separate browser/MVCR failures from RabbitMQ publishing failures in dashboards and incident debugging. Alert from `mvcr_fetcher_requests_total` for user-visible fetcher failures.

### `mvcr_fetcher_request_state`

Gauge for current fetcher request state.

Labels:

- `state`: `waiting` or `locked`

This should mirror the current internal `request_state` data from `MetricsCollector`. Treat this as dashboard/debug context, not a primary alert source.

### `mvcr_fetcher_target_latency_seconds`

Gauge for latency to the MVCR target website.

This matches the current average-latency behavior in `MetricsCollector`. Treat this as dashboard/debug context unless latency proves to be a reliable incident signal.

### `mvcr_fetcher_target_up`

Gauge for MVCR target website connectivity.

Values:

- `1`: target check is healthy
- `0`: target check is failing

Use this to alert when all fetchers agree that the MVCR target website is unreachable or unhealthy.

### `mvcr_fetcher_last_success_timestamp_seconds`

Gauge containing the Unix timestamp of the last successful fetch.

Use this to alert when no fetch has succeeded for too long while the fetcher has had recent request activity.

### `mvcr_fetcher_build_info`

Info-style gauge with value `1`.

Labels:

- `version`
- `commit`

## Bot Metrics

### `mvcr_bot_errors_total`

Counter for bot-side operational errors.

Labels:

- `stage`: `telegram`, `rabbitmq`, `db`, `notification_dispatcher`, `scheduler`, or another stable stage
- `error_type`: stable category such as `network`, `timeout`, `publish_failed`, `db_error`, `unexpected`, or `permanent_send_error`

Use this for bot operational error burst alerts with a threshold high enough to avoid transient network noise.

### `mvcr_bot_notifications_total`

Counter for notification delivery outcomes.

Labels:

- `notification_kind`: `status_change`, `failed_fetch`, `force_refresh_unchanged`, or `expiration`
- `result`: `ok`, `dead_user`, `retryable_gave_up`, or `permanent_other`

Use this for Telegram delivery failure alerts. `dead_user` is an expected product outcome and should not page; alert on repeated `retryable_gave_up` or `permanent_other` results.

### `mvcr_bot_rabbitmq_messages_total`

Counter for bot RabbitMQ message handling.

Labels:

- `queue`: `StatusUpdateQueue`, `ExpirationQueue`, or `FetcherMetricsQueue`
- `result`: `processed`, `failed`, `dropped`, or `ignored`

Use this for dashboards and incident debugging. RabbitMQ queue pileup and no-consumer alerts are the primary alerting signals for queue-consumer problems.

### `mvcr_bot_published_messages_total`

Counter for messages the bot publishes to RabbitMQ.

Labels:

- `queue`: destination queue
- `result`: `published`, `duplicate_skipped`, or `failed`

### `mvcr_bot_scheduler_runs_total`

Counter for scheduled monitor loop runs.

Labels:

- `scheduler`: `application_monitor`, `reminder_monitor`, or `notification_dispatcher`
- `result`: `success` or `failed`

### `mvcr_bot_build_info`

Info-style gauge with value `1`.

Labels:

- `version`
- `commit`

## Alert Examples

These examples assume Prometheus scrapes bot and fetcher `/metrics` endpoints and the chart installs `PrometheusRule` resources.

### Fetcher Hard Failures

Fires when too many fetch requests fully fail after queue-level retries are exhausted.

```yaml
- alert: MvcrFetcherHardFailures
  expr: |
    sum(increase(mvcr_fetcher_requests_total{result="failed"}[30m])) >= 25
  for: 10m
  labels:
    severity: critical
    service: mvcr
  annotations:
    summary: "MVCR fetcher has many exhausted failures"
    description: "Fetcher produced {{ $value }} request(s) that exhausted MAX_RETRIES during the last 30 minutes."
```

### Fetcher Retry Storm

Fires when a sustained majority of fetcher traffic is retrying or failing.

```yaml
- alert: MvcrFetcherRetryStorm
  expr: |
    (
      sum(rate(mvcr_fetcher_requests_total{result=~"retry|failed"}[30m]))
      /
      clamp_min(sum(rate(mvcr_fetcher_requests_total[30m])), 0.001)
    ) > 0.7
    and
    sum(increase(mvcr_fetcher_requests_total{result=~"retry|failed"}[30m])) >= 30
  for: 15m
  labels:
    severity: warning
    service: mvcr
  annotations:
    summary: "MVCR fetcher retry/failure storm"
    description: "More than 70% of fetcher requests are retrying or failing, with at least 30 retry/failure events in 30 minutes."
```

### Fetcher Target Down

Fires when all fetchers report that they cannot reach the MVCR target website.

```yaml
- alert: MvcrFetcherTargetDown
  expr: |
    max(mvcr_fetcher_target_up) == 0
  for: 10m
  labels:
    severity: critical
    service: mvcr
  annotations:
    summary: "MVCR target website check is failing"
    description: "All reporting fetchers say that the MVCR target website is unreachable or unhealthy."
```

### Fetcher No Successful Fetch

Fires when no successful fetch has happened recently, but fetchers have still seen request activity.

```yaml
- alert: MvcrFetcherNoSuccessfulFetch
  expr: |
    (time() - max(mvcr_fetcher_last_success_timestamp_seconds) > 7200)
    and
    sum(increase(mvcr_fetcher_requests_total[2h])) > 0
  for: 10m
  labels:
    severity: critical
    service: mvcr
  annotations:
    summary: "No successful MVCR fetch for over two hours"
    description: "Fetchers have had request activity, but no fetcher has reported a successful fetch for more than two hours."
```

### Bot Error Burst

Fires when the bot records repeated operational errors.

```yaml
- alert: MvcrBotErrorBurst
  expr: |
    sum(increase(mvcr_bot_errors_total[15m])) >= 10
  for: 10m
  labels:
    severity: warning
    service: mvcr
  annotations:
    summary: "MVCR bot error burst"
    description: "Bot recorded {{ $value }} operational error(s) during the last 15 minutes."
```

### Bot Notification Delivery Failures

Fires when Telegram notification delivery repeatedly fails.

```yaml
- alert: MvcrTelegramDeliveryFailures
  expr: |
    sum(increase(mvcr_bot_notifications_total{result=~"retryable_gave_up|permanent_other"}[30m])) >= 10
  for: 10m
  labels:
    severity: warning
    service: mvcr
  annotations:
    summary: "MVCR bot notification delivery failures"
    description: "Bot had {{ $value }} notification(s) fail delivery during the last 30 minutes."
```

