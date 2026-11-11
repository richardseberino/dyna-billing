#!/usr/bin/env bash
# Cria (uma única vez) o bucket S3 e a tabela DynamoDB do state remoto do Terraform
# e gera infra/terraform/backend.hcl.
#   uso: infra/scripts/bootstrap-state.sh [regiao] [projeto]
source "$(dirname "$0")/common.sh"
need aws

REGION="${1:-sa-east-1}"
PROJECT="${2:-dyna-billing}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${PROJECT}-tfstate-${ACCOUNT}-${REGION}"
TABLE="${PROJECT}-tflock"

log "Conta $ACCOUNT, região $REGION"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  log "Bucket $BUCKET já existe"
else
  log "Criando bucket $BUCKET"
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
fi
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
  log "Tabela $TABLE já existe"
else
  log "Criando tabela de lock $TABLE"
  aws dynamodb create-table --table-name "$TABLE" --region "$REGION" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE" --region "$REGION"
fi

cat > "$TF_DIR/backend.hcl" <<HCL
bucket         = "$BUCKET"
key            = "$PROJECT/terraform.tfstate"
region         = "$REGION"
dynamodb_table = "$TABLE"
encrypt        = true
HCL
log "Gerado $TF_DIR/backend.hcl"
