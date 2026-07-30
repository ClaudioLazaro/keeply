{{/*
Chart name, truncated at 63 chars (DNS label limit).
*/}}
{{- define "keep-aiops.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name.
*/}}
{{- define "keep-aiops.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "keep-aiops.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Component resource names.
*/}}
{{- define "keep-aiops.apiName" -}}
{{- printf "%s-api" (include "keep-aiops.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "keep-aiops.gatewayName" -}}
{{- printf "%s-mcp-gateway" (include "keep-aiops.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "keep-aiops.migrateName" -}}
{{- printf "%s-migrate" (include "keep-aiops.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels + per-component selector labels.
*/}}
{{- define "keep-aiops.labels" -}}
helm.sh/chart: {{ include "keep-aiops.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: keep-aiops
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end -}}

{{- define "keep-aiops.apiSelectorLabels" -}}
app.kubernetes.io/name: {{ include "keep-aiops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: aiops-api
{{- end -}}

{{- define "keep-aiops.gatewaySelectorLabels" -}}
app.kubernetes.io/name: {{ include "keep-aiops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: mcp-gateway
{{- end -}}

{{/*
Image reference (tag defaults to appVersion).
*/}}
{{- define "keep-aiops.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/*
mcp-gateway ServiceAccount name.
*/}}
{{- define "keep-aiops.gatewayServiceAccountName" -}}
{{- if .Values.mcpGateway.serviceAccount.create -}}
{{- default (include "keep-aiops.gatewayName" .) .Values.mcpGateway.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.mcpGateway.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Bundled postgresql host / secret names (bitnami subchart naming).
*/}}
{{- define "keep-aiops.postgresql.host" -}}
{{- printf "%s-postgresql" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "keep-aiops.postgresql.secretName" -}}
{{- printf "%s-postgresql" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
AIOPS_DATABASE_URL env block, shared by the aiops-api Deployment and the
migrate Job. Resolution: existingSecret > explicit url > bundled subchart.
Fails the render when no database is configured.
*/}}
{{- define "keep-aiops.databaseEnv" -}}
{{- if .Values.database.existingSecret }}
- name: AIOPS_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret }}
      key: {{ .Values.database.existingSecretKey }}
{{- else if .Values.database.url }}
- name: AIOPS_DATABASE_URL
  value: {{ .Values.database.url | quote }}
{{- else if .Values.postgresql.enabled }}
- name: AIOPS_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "keep-aiops.postgresql.secretName" . }}
      key: postgres-password
- name: AIOPS_DATABASE_URL
  value: "postgresql+psycopg://postgres:$(AIOPS_DB_PASSWORD)@{{ include "keep-aiops.postgresql.host" . }}:5432/{{ .Values.postgresql.auth.database }}"
{{- else }}
{{- fail "no database configured: set postgresql.enabled=true, database.url, or database.existingSecret" }}
{{- end }}
{{- end -}}
