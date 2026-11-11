#!/usr/bin/env bash
# Executa o coletor agora (fora do agendamento) e acompanha o log.
#   uso: infra/scripts/run-collector.sh [argumentos do coletor]
#   ex.: infra/scripts/run-collector.sh --from 2026-03-31 --to 2026-06-30
source "$(dirname "$0")/common.sh"
need aws terraform python3
tf_init
REGION=$(region)
CLUSTER=$(tf_out ecs_cluster)
TASKDEF=$(tf_out collector_task_definition)
SUBNETS=$(tf_out private_subnets)
SG=$(tf_out app_security_group)

CMD=$(python3 -c 'import json,sys;print(json.dumps(["python","-m","dynabilling.collector","--once"]+sys.argv[1:]))' "$@")

TASK=$(aws ecs run-task --region "$REGION" --cluster "$CLUSTER" --task-definition "$TASKDEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"collector\",\"command\":$CMD}]}" \
  --query 'tasks[0].taskArn' --output text)
log "Tarefa iniciada: ${TASK##*/}"
aws ecs wait tasks-stopped --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK"
CODE=$(aws ecs describe-tasks --region "$REGION" --cluster "$CLUSTER" --tasks "$TASK" \
  --query 'tasks[0].containers[0].exitCode' --output text)
aws logs tail "/ecs/$(tf_out ecs_cluster)/collector" --region "$REGION" --since 30m | tail -20
[[ "$CODE" == "0" ]] && log "Coleta concluída" || die "coletor terminou com código $CODE"
