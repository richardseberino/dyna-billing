#!/usr/bin/env bash
# Remove o ambiente. O RDS tem deletion_protection: desligue antes com
#   terraform.tfvars -> db_deletion_protection = false  e  infra/scripts/deploy.sh
# Um snapshot final do banco é mantido.
source "$(dirname "$0")/common.sh"
need terraform
tf_init
read -rp "Digite 'destruir' para remover TODO o ambiente: " ok
[[ "$ok" == "destruir" ]] || die "cancelado"
tf destroy -input=false
