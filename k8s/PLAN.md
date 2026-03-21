# Helm Chart Plan: mvcr-application-checker

## Overview

Custom Helm chart (scaffolded via `helm create`) for deploying the MVCR Application Checker stack.
All templates are self-contained — **no subchart dependencies**. PostgreSQL and RabbitMQ are
templated directly as StatefulSets, with a flag to switch to external instances instead.

### Why no subcharts

Research showed no viable non-operator, non-Bitnami charts worth depending on:
- **CNPG**: Great, but requires a cluster-wide operator pre-installed — too heavy for this use case
- **Zalando/Crunchy**: Operator-only, no Helm subchart at all
- **groundhog2k**: Single maintainer, moderate adoption
- **SolidCharts**: Too young (v0.x)
- **Bitnami**: Dead (paid-only repos)

Our manifests are simple single-instance deployments. Templating them ourselves is straightforward
and gives full control with zero external dependency.

---

## Chart Structure

```
k8s/mvcr-application-checker/
├── Chart.yaml                  # no dependencies
├── values.yaml                 # main values
├── values.sample.yaml          # example with placeholder secrets
├── templates/
│   ├── _helpers.tpl            # standard helpers + connection wiring
│   ├── bot/
│   │   ├── statefulset.yaml      # single replica, no scaling
│   │   └── configmap.yaml
│   ├── fetcher/
│   │   ├── deployment.yaml
│   │   └── configmap.yaml
│   ├── postgresql/
│   │   ├── statefulset.yaml
│   │   ├── service.yaml
│   │   ├── pvc.yaml
│   │   └── configmap-init.yaml   # init.sql
│   ├── rabbitmq/
│   │   ├── statefulset.yaml
│   │   ├── service.yaml
│   │   ├── pvc.yaml
│   │   └── configmap.yaml        # rabbitmq.conf
│   ├── secrets.yaml              # all secrets (credentials + SSL certs)
│   ├── cronjob-db-dump.yaml
│   └── NOTES.txt
└── .helmignore
```

### Template grouping

Each component gets its own subdirectory. Shared resources (secrets, cronjob) stay at root.
All infra templates (postgresql/, rabbitmq/) are gated behind `{{- if .Values.<component>.enabled }}`.

---

## values.yaml Design

### Global / Chart-level

```yaml
nameOverride: ""
fullnameOverride: ""
```

### Bot

```yaml
bot:
  enabled: true
  image:
    repository: olegeech/mvcr-application-checker
    tag: bot-latest
    pullPolicy: Always
  # No replicas knob — always exactly 1 (StatefulSet)

  config:
    ADMIN_CHAT_IDS: "12345, 678910"
    LOG_LEVEL: "INFO"
    REFRESH_PERIOD: "3600"
    SCHEDULER_PERIOD: "300"
    REQUEUE_THRESHOLD_SECONDS: "3600"
    NOT_FOUND_MAX_DAYS: "30"
    NOT_FOUND_REFRESH_PERIOD: "86400"
    # proxy:
    #   HTTPS_PROXY: ""
    #   HTTP_PROXY: ""
    #   ALL_PROXY: ""

  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 250m
      memory: 512Mi
```

### Fetcher

```yaml
fetcher:
  enabled: true
  image:
    repository: olegeech/mvcr-application-checker
    tag: fetcher-latest
  replicas: 1
  privileged: true              # needed for headless browser

  config:
    URL: "https://ipc.gov.cz/informace-o-stavu-rizeni/"
    RETRY_INTERVAL: "30"
    PAGE_LOAD_LIMIT_SECONDS: "20"
    CAPTCHA_WAIT_SECONDS: "120"
    JITTER_SECONDS: "600"
    MAX_MESSAGES: "10"
    MAX_RETRIES: "5"

  resources: {}
```

### DB Dump CronJob

```yaml
dbDump:
  enabled: true
  schedule: "15 2 * * *"
  timeZone: "Europe/Prague"
  image:
    repository: postgres
    tag: "15.4"
  retainCount: 5
  persistence:
    size: 10Gi
    storageClass: ""
    existingClaim: ""
```

### PostgreSQL (integrated vs external)

```yaml
postgresql:
  enabled: true                 # false = use externalDatabase
  image:
    repository: postgres
    tag: "15.4"
  database: "AppTrackerDB"
  user: "postgres"
  # password in secrets section
  persistence:
    size: 10Gi
    storageClass: ""
    existingClaim: ""
  resources:
    requests:
      cpu: 250m
      memory: 512Mi
    limits:
      cpu: 500m
      memory: 1Gi
  service:
    name: apptrackerdb          # override service name if needed

externalDatabase:
  host: ""
  port: 5432
  database: "AppTrackerDB"
  user: "postgres"
  # password in secrets section
```

### RabbitMQ (integrated vs external)

```yaml
rabbitmq:
  enabled: true                 # false = use externalRabbitmq
  image:
    repository: rabbitmq
    tag: "3.12-management"
  user: "bunny_admin"
  # password in secrets section
  persistence:
    size: 10Gi
    storageClass: ""
    existingClaim: ""
  resources:
    requests:
      cpu: 250m
      memory: 512Mi
    limits:
      cpu: 500m
      memory: 1Gi
  ssl:
    enabled: true
    # certs in secrets section
  service:
    type: LoadBalancer
    annotations: {}
    # e.g. external-dns.alpha.kubernetes.io/hostname: mvcr.example.com

externalRabbitmq:
  host: ""
  port: 5671
  user: "admin"
  ssl:
    enabled: true
    existingSecret: ""          # secret with ca.crt, client.crt, client.key
  # password in secrets section
```

### Secrets

```yaml
secrets:
  telegramBotToken: ""
  dbPassword: ""
  rabbitPassword: ""
  rabbitSSL:
    existingSecret: ""          # if set, skip creating SSL secret
    caCrt: ""
    serverCrt: ""               # for integrated rabbitmq
    serverKey: ""               # for integrated rabbitmq
    clientCrt: ""               # for fetcher -> rabbitmq
    clientKey: ""               # for fetcher -> rabbitmq
```

---

## Template Logic

### Connection wiring (_helpers.tpl)

Helper functions resolve connection details based on integrated vs external mode:

```
{{- define "chart.db.host" -}}
{{- if .Values.postgresql.enabled -}}
  {{- include "chart.fullname" . }}-postgresql
{{- else -}}
  {{- .Values.externalDatabase.host -}}
{{- end -}}
{{- end -}}

{{- define "chart.db.port" -}}
{{- if .Values.postgresql.enabled -}}5432{{- else -}}{{ .Values.externalDatabase.port }}{{- end -}}
{{- end -}}

{{- define "chart.db.name" -}}
{{- if .Values.postgresql.enabled -}}
  {{- .Values.postgresql.database -}}
{{- else -}}
  {{- .Values.externalDatabase.database -}}
{{- end -}}
{{- end -}}

{{- define "chart.db.user" -}}
{{- if .Values.postgresql.enabled -}}
  {{- .Values.postgresql.user -}}
{{- else -}}
  {{- .Values.externalDatabase.user -}}
{{- end -}}
{{- end -}}
```

Same pattern for `chart.rabbit.host`, `chart.rabbit.port`, `chart.rabbit.user`.

Bot and fetcher configmaps use these helpers — they don't care where the DB/rabbit lives.

### Secrets

Single Secret with all credentials. Gated keys:
- `TELEGRAM_BOT_TOKEN` — always
- `DB_PASSWORD` — always (needed regardless of integrated/external)
- `RABBIT_PASSWORD` — always

Separate Secret for SSL certs (or use `existingSecret`):
- Server certs (ca, server.crt, server.key) — only when `rabbitmq.enabled && rabbitmq.ssl.enabled`
- Client certs (ca, client.crt, client.key) — for fetcher, always when SSL is on

### Integrated PostgreSQL

- StatefulSet with single replica
- Init SQL via ConfigMap mounted to `/docker-entrypoint-initdb.d`
- PVC for `/var/lib/postgresql/data`
- ClusterIP Service

### Integrated RabbitMQ

- StatefulSet with single replica
- Config via ConfigMap (`rabbitmq.conf` for SSL)
- SSL certs via Secret volume mount
- PVC for `/var/lib/rabbitmq`
- Service (type configurable, default LoadBalancer)

### CronJob (db-dump)

- Uses `chart.db.*` helpers for connection
- PVC for dump storage (or existingClaim)
- Configurable retain count

---

## Implementation Order

1. `helm create` scaffold, clean up nginx defaults
2. `_helpers.tpl` — standard helpers + db/rabbit connection helpers
3. `secrets.yaml` + SSL secrets
4. `postgresql/` templates (StatefulSet, Service, PVC, init ConfigMap)
5. `rabbitmq/` templates (StatefulSet, Service, PVC, config ConfigMap)
6. `bot/` templates (Deployment, ConfigMap)
7. `fetcher/` templates (Deployment, ConfigMap)
8. `cronjob-db-dump.yaml`
9. `NOTES.txt`
10. `values.sample.yaml`
11. `helm template` test — integrated mode
12. `helm template` test — external mode
