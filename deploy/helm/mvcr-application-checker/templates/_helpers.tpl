{{/*
Expand the name of the chart.
*/}}
{{- define "mvcr-application-checker.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "mvcr-application-checker.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "mvcr-application-checker.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "mvcr-application-checker.labels" -}}
helm.sh/chart: {{ include "mvcr-application-checker.chart" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels for a specific component.
Usage: {{ include "mvcr-application-checker.selectorLabels" (dict "context" . "component" "bot") }}
*/}}
{{- define "mvcr-application-checker.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mvcr-application-checker.name" .context }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component labels (common + selector).
Usage: {{ include "mvcr-application-checker.componentLabels" (dict "context" . "component" "bot") }}
*/}}
{{- define "mvcr-application-checker.componentLabels" -}}
{{ include "mvcr-application-checker.labels" .context }}
{{ include "mvcr-application-checker.selectorLabels" (dict "context" .context "component" .component) }}
{{- end }}

{{/* ---- Database connection helpers ---- */}}

{{- define "mvcr-application-checker.db.host" -}}
{{- if .Values.postgresql.enabled -}}
{{- if .Values.postgresql.service.name -}}
{{- .Values.postgresql.service.name -}}
{{- else -}}
{{- printf "%s-postgresql" (include "mvcr-application-checker.fullname" .) -}}
{{- end -}}
{{- else -}}
{{- required "externalDatabase.host is required when postgresql.enabled=false" .Values.externalDatabase.host -}}
{{- end -}}
{{- end -}}

{{- define "mvcr-application-checker.db.port" -}}
{{- if .Values.postgresql.enabled -}}
5432
{{- else -}}
{{- .Values.externalDatabase.port | default 5432 -}}
{{- end -}}
{{- end -}}

{{- define "mvcr-application-checker.db.name" -}}
{{- if .Values.postgresql.enabled -}}
{{- .Values.postgresql.database -}}
{{- else -}}
{{- .Values.externalDatabase.database -}}
{{- end -}}
{{- end -}}

{{- define "mvcr-application-checker.db.user" -}}
{{- if .Values.postgresql.enabled -}}
{{- .Values.postgresql.user -}}
{{- else -}}
{{- .Values.externalDatabase.user -}}
{{- end -}}
{{- end -}}

{{/* ---- RabbitMQ connection helpers ---- */}}

{{- define "mvcr-application-checker.rabbit.host" -}}
{{- if .Values.rabbitmq.enabled -}}
{{- printf "%s-rabbitmq" (include "mvcr-application-checker.fullname" .) -}}
{{- else -}}
{{- required "externalRabbitmq.host is required when rabbitmq.enabled=false" .Values.externalRabbitmq.host -}}
{{- end -}}
{{- end -}}

{{- define "mvcr-application-checker.rabbit.port" -}}
{{- if .Values.rabbitmq.enabled -}}
{{- if .Values.rabbitmq.ssl.enabled -}}
5671
{{- else -}}
5672
{{- end -}}
{{- else -}}
{{- .Values.externalRabbitmq.port | default 5671 -}}
{{- end -}}
{{- end -}}

{{- define "mvcr-application-checker.rabbit.user" -}}
{{- if .Values.rabbitmq.enabled -}}
{{- .Values.rabbitmq.user -}}
{{- else -}}
{{- .Values.externalRabbitmq.user -}}
{{- end -}}
{{- end -}}

{{/* ---- SSL helpers ---- */}}

{{/*
Name of the secret containing RabbitMQ SSL certs.
*/}}
{{- define "mvcr-application-checker.rabbit.sslSecretName" -}}
{{- if .Values.secrets.rabbitSSL.existingSecret -}}
{{- .Values.secrets.rabbitSSL.existingSecret -}}
{{- else -}}
{{- printf "%s-rabbit-ssl" (include "mvcr-application-checker.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Whether RabbitMQ SSL is active (integrated or external).
*/}}
{{- define "mvcr-application-checker.rabbit.sslEnabled" -}}
{{- if .Values.rabbitmq.enabled -}}
{{- .Values.rabbitmq.ssl.enabled -}}
{{- else -}}
{{- .Values.externalRabbitmq.ssl.enabled -}}
{{- end -}}
{{- end -}}

{{/*
Name of the credentials secret.
*/}}
{{- define "mvcr-application-checker.secretName" -}}
{{- printf "%s-credentials" (include "mvcr-application-checker.fullname" .) -}}
{{- end -}}
