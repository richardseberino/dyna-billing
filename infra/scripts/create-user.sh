#!/usr/bin/env bash
# Gerencia usuários da interface web dentro da tarefa em execução (ECS Exec).
# Requer o Session Manager plugin: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html
#   uso: infra/scripts/create-user.sh <usuario>               # cria (pede a senha)
#        infra/scripts/create-user.sh <usuario> passwd|disable|enable|delete
#        infra/scripts/create-user.sh - list
source "$(dirname "$0")/common.sh"
need aws terraform session-manager-plugin
[[ $# -ge 1 ]] || die "uso: $0 <usuario> [add|passwd|disable|enable|delete] | $0 - list"
USER_NAME="$1"; ACTION="${2:-add}"
[[ "$USER_NAME" == "-" ]] && ACTION="list"
tf_init
REGION=$(region)
CLUSTER=$(tf_out ecs_cluster); SERVICE=$(tf_out web_service)
TASK=$(aws ecs list-tasks --region "$REGION" --cluster "$CLUSTER" --service-name "$SERVICE" \
  --desired-status RUNNING --query 'taskArns[0]' --output text)
[[ "$TASK" != "None" ]] || die "nenhuma tarefa web em execução"

if [[ "$ACTION" == "list" ]]; then CMD="python -m dynabilling.auth list"
else CMD="python -m dynabilling.auth $ACTION $USER_NAME"; fi

aws ecs execute-command --region "$REGION" --cluster "$CLUSTER" --task "$TASK" \
  --container web --interactive --command "$CMD"
