{{/* Common labels and names for postgres-aegis */}}

{{- define "postgres-aegis.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "postgres-aegis.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "postgres-aegis.labels" -}}
app.kubernetes.io/name: {{ include "postgres-aegis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: postgres-aegis
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "postgres-aegis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "postgres-aegis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "postgres-aegis.writerName" -}}
{{ include "postgres-aegis.fullname" . }}-writer
{{- end -}}

{{- define "postgres-aegis.readerName" -}}
{{ include "postgres-aegis.fullname" . }}-reader
{{- end -}}
