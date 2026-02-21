{{- define "agentic-primitives-gateway.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agentic-primitives-gateway.fullname" -}}
{{- if contains .Chart.Name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "agentic-primitives-gateway.labels" -}}
app.kubernetes.io/name: {{ include "agentic-primitives-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "agentic-primitives-gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agentic-primitives-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
