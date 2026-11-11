#!/usr/bin/env bash
# Grava as credenciais Dynatrace no Secrets Manager (não passam pelo state do Terraform).
# Ordem de leitura: variáveis de ambiente -> .env da raiz -> pergunta interativa.
#   uso: infra/scripts/set-dynatrace-secret.sh
source "$(dirname "$0")/common.sh"
need aws python3 terraform
tf_init
SECRET_ARN=$(tf_out dynatrace_secret_arn)
REGION=$(region)

DT_CLIENT_ID="${DT_CLIENT_ID:-$(env_get DT_CLIENT_ID)}"
DT_CLIENT_SECRET="${DT_CLIENT_SECRET:-$(env_get DT_CLIENT_SECRET)}"
DT_BEARER_TOKEN="${DT_BEARER_TOKEN:-$(env_get DT_BEARER_TOKEN)}"

if [[ -z "$DT_CLIENT_ID$DT_BEARER_TOKEN" ]]; then
  read -rp "DT_CLIENT_ID: " DT_CLIENT_ID
  read -rsp "DT_CLIENT_SECRET: " DT_CLIENT_SECRET; echo
fi
[[ -n "$DT_CLIENT_ID$DT_BEARER_TOKEN" ]] || die "nenhuma credencial informada"

TMP=$(mktemp); chmod 600 "$TMP"; trap 'rm -f "$TMP" "${_TF_AWS_CONFIG:-}"' EXIT
DT_CLIENT_ID="$DT_CLIENT_ID" DT_CLIENT_SECRET="$DT_CLIENT_SECRET" DT_BEARER_TOKEN="$DT_BEARER_TOKEN" \
  python3 -c 'import json,os;print(json.dumps({k:os.environ.get(k,"") for k in ("DT_CLIENT_ID","DT_CLIENT_SECRET","DT_BEARER_TOKEN")}))' > "$TMP"

aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ARN" \
  --secret-string "file://$TMP" >/dev/null
log "Credenciais Dynatrace gravadas em $SECRET_ARN"
