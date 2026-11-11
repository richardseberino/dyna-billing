#!/usr/bin/env bash
# Funções e variáveis compartilhadas pelos scripts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
export AWS_PAGER=""

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[aviso]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[erro]\033[0m %s\n' "$*" >&2; exit 1; }

need() { for c in "$@"; do command -v "$c" >/dev/null || die "comando '$c' não encontrado"; done; }

# Credenciais para o Terraform.
# Versões antigas do Terraform/provider não entendem `aws login` (login_session) nem todos os
# formatos de SSO. Em vez de exportar chaves temporárias (expiram em ~15 min, menos que a criação
# do RDS), o Terraform recebe um perfil com credential_process que pede as credenciais ao AWS CLI
# sempre que precisar; elas são renovadas automaticamente durante applies longos.
# Se já houver AWS_ACCESS_KEY_ID no ambiente (ex.: CI), nada muda.
_tf_aws_config() {
  [[ -n "${_TF_AWS_CONFIG:-}" ]] && return
  local src_profile="${AWS_PROFILE:-default}"
  local real_cfg="${AWS_CONFIG_FILE:-$HOME/.aws/config}"
  _TF_AWS_CONFIG="$(mktemp -t dyna-tf-aws)"
  cat > "$_TF_AWS_CONFIG" <<CFG
[profile dyna-billing-tf]
credential_process = env AWS_CONFIG_FILE=$real_cfg aws configure export-credentials --profile $src_profile --format process
CFG
  trap 'rm -f "$_TF_AWS_CONFIG"' EXIT
}

# terraform no diretório certo, com o backend gerado pelo bootstrap-state.sh
tf() {
  if [[ -n "${AWS_ACCESS_KEY_ID:-}" ]]; then
    terraform -chdir="$TF_DIR" "$@"
  else
    _tf_aws_config
    AWS_CONFIG_FILE="$_TF_AWS_CONFIG" AWS_PROFILE=dyna-billing-tf AWS_SDK_LOAD_CONFIG=1 \
      terraform -chdir="$TF_DIR" "$@"
  fi
}

tf_init() {
  [[ -f "$TF_DIR/backend.hcl" ]] || die "backend.hcl não existe: rode infra/scripts/bootstrap-state.sh primeiro"
  [[ -f "$TF_DIR/terraform.tfvars" ]] || die "crie $TF_DIR/terraform.tfvars a partir de terraform.tfvars.example"
  tf init -input=false -backend-config=backend.hcl >/dev/null
}

tf_out() { tf output -raw "$1"; }

region() { awk -F'"' '/^aws_region/{print $2}' "$TF_DIR/terraform.tfvars" | head -1; }

# lê uma chave do .env da raiz (sem executar o arquivo)
env_get() { [[ -f "$ROOT/.env" ]] && grep -E "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- || true; }
