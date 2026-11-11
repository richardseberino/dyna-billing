#!/usr/bin/env bash
# Build da imagem (arm64), push no ECR e terraform apply. Use para o primeiro deploy e para atualizações.
#   uso: infra/scripts/deploy.sh [--auto-approve]
source "$(dirname "$0")/common.sh"
need aws docker terraform git python3
APPROVE=("$@")
REGION=$(region)

tf_init

log "1/5 Repositório ECR e segredos base"
tf apply -input=false ${APPROVE[@]+"${APPROVE[@]}"} \
  -target=aws_ecr_repository.app -target=aws_ecr_lifecycle_policy.app \
  -target=aws_secretsmanager_secret_version.dynatrace_placeholder

log "2/5 Credenciais Dynatrace"
SECRET_ARN=$(tf_out dynatrace_secret_arn)
HAS_CREDS=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ARN" \
  --query SecretString --output text | python3 -c 'import json,sys;d=json.load(sys.stdin);print(int(bool(d.get("DT_CLIENT_ID") or d.get("DT_BEARER_TOKEN"))))')
if [[ "$HAS_CREDS" != "1" ]]; then
  "$(dirname "$0")/set-dynatrace-secret.sh"
else
  log "já configuradas (para trocar: infra/scripts/set-dynatrace-secret.sh)"
fi

log "3/5 Build e push da imagem"
REPO=$(tf_out ecr_repository_url)
TAG="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
if [[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]]; then
  TAG="${TAG}-dirty-$(date +%Y%m%d%H%M%S)"
  warn "há alterações não commitadas; tag $TAG"
fi
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
if aws ecr describe-images --region "$REGION" --repository-name "${REPO##*/}" --image-ids imageTag="$TAG" >/dev/null 2>&1; then
  log "imagem $TAG já existe no ECR"
else
  docker buildx build --platform linux/arm64 -t "$REPO:$TAG" --push "$ROOT/app"
fi

log "4/5 Infraestrutura completa (tag $TAG)"
tf apply -input=false ${APPROVE[@]+"${APPROVE[@]}"} -var "image_tag=$TAG"

log "5/5 Aguardando o serviço web estabilizar"
CLUSTER=$(tf_out ecs_cluster); SERVICE=$(tf_out web_service)
aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "$SERVICE"

log "Pronto: $(tf_out app_url)"
cat <<TXT

Próximos passos (primeiro deploy):
  infra/scripts/run-collector.sh            # carga inicial sem esperar o agendamento
  infra/scripts/create-user.sh admin        # cria o primeiro usuário da interface
TXT
