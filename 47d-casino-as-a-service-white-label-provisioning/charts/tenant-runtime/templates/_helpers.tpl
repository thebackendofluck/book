{{/*
Expand the name of the chart.
*/}}
{{- define "tenant-runtime.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully-qualified release name, incorporating the tenant slug to guarantee
uniqueness across multi-tenant deployments.
Format: <release-name>-<tenant-slug>  (truncated to 63 chars)
*/}}
{{- define "tenant-runtime.fullname" -}}
{{- $slug := required "tenant.slug is required" .Values.tenant.slug }}
{{- printf "%s-%s" .Release.Name $slug | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Target namespace: casino-<tenant.slug>
*/}}
{{- define "tenant-runtime.namespace" -}}
{{- $slug := required "tenant.slug is required" .Values.tenant.slug }}
{{- printf "casino-%s" $slug | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Chart label (name + version).
*/}}
{{- define "tenant-runtime.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource in this release.
The caas.tenant label is the primary tenant selector used by monitoring,
network policies, and cost-allocation tooling.
*/}}
{{- define "tenant-runtime.labels" -}}
helm.sh/chart: {{ include "tenant-runtime.chart" . }}
{{ include "tenant-runtime.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
caas.jurisdiction: {{ .Values.tenant.jurisdiction | quote }}
{{- end }}

{{/*
Selector labels (stable subset used in matchLabels — never change after first deploy).
*/}}
{{- define "tenant-runtime.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tenant-runtime.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
caas.tenant: {{ required "tenant.slug is required" .Values.tenant.slug | quote }}
{{- end }}

{{/*
Image reference helper.
*/}}
{{- define "tenant-runtime.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end }}
