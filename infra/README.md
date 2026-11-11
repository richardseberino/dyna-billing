# Deploy na AWS

```
                 allowed_cidrs (rede corporativa / VPN)
                              │ HTTPS 443 (80 → 301)
┌─────────────────────────────▼──────────────────────────────── VPC 10.42.0.0/16 ┐
│  subnets públicas    ┌───────────────┐        ┌─────────────┐                  │
│                      │      ALB      │        │ NAT Gateway │──▶ Internet      │
│                      └───────┬───────┘        └──────▲──────┘  (Dynatrace API, │
│  subnets privadas            │ :8000                 │          Banco Central) │
│        ┌─────────────────────▼──┐   ┌────────────────┴───────┐                 │
│        │ ECS Fargate: web       │   │ ECS Fargate: coletor   │◀── EventBridge  │
│        │ (serviço, 1 tarefa)    │   │ (tarefa a cada 6 h)    │    Scheduler    │
│        └───────────┬────────────┘   └───────────┬────────────┘                 │
│                    └──────────┬─────────────────┘                              │
│                     ┌─────────▼─────────┐                                      │
│                     │  RDS MySQL 8.4    │  criptografado, backup 7 dias         │
│                     └───────────────────┘                                      │
└────────────────────────────────────────────────────────────────────────────────┘
   ECR (imagem arm64) · Secrets Manager (senha do banco, chave de sessão, Dynatrace) · CloudWatch Logs
```

## O que é criado

| Recurso | Detalhe |
|---|---|
| Rede | VPC com 2 AZs, subnets públicas e privadas, 1 NAT Gateway |
| ALB | HTTPS com o certificado ACM informado; HTTP redireciona para HTTPS. Entrada liberada só para `allowed_cidrs` |
| ECS Fargate (ARM64/Graviton) | Serviço `web` com 1 tarefa e ECS Exec habilitado. Tarefa `collector` agendada pelo EventBridge Scheduler |
| RDS MySQL 8.4 | `db.t4g.micro`, gp3 criptografado, sem acesso público, backups de 7 dias, `deletion_protection` e snapshot final |
| Secrets Manager | Senha do banco e chave de sessão, geradas pelo Terraform. Credenciais Dynatrace, gravadas pelo script e fora do state |
| ECR | Tags imutáveis, scan no push, mantém as 20 últimas imagens |
| CloudWatch Logs | `/ecs/dyna-billing/web` e `/ecs/dyna-billing/collector`, retenção de 90 dias |

O schema do banco é criado pela própria aplicação ao iniciar (`dynabilling.migrate`). O processo é idempotente.

## Pré-requisitos

- AWS CLI v2 autenticada (`aws sso login` ou equivalente), com permissão para criar os recursos acima
- Terraform 1.5 ou superior
- Docker com buildx
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html), necessário apenas para o `create-user.sh`
- Um certificado ACM emitido **na mesma região** do deploy, para o HTTPS

## Primeiro deploy

```bash
# 1. state remoto do Terraform: bucket S3 + lock no DynamoDB (uma vez por conta/região)
infra/scripts/bootstrap-state.sh sa-east-1

# 2. variáveis
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
#    edite: allowed_cidrs, acm_certificate_arn, domain_name/route53_zone_id e app_env

# 3. build, push e infraestrutura (pede as credenciais Dynatrace se não houver .env)
infra/scripts/deploy.sh

# 4. carga inicial dos dados (sem esperar o agendamento) e primeiro usuário
infra/scripts/run-collector.sh
infra/scripts/create-user.sh admin
```

No primeiro deploy, o `deploy.sh` lê `DT_CLIENT_ID` e `DT_CLIENT_SECRET` do `.env` da raiz e grava no Secrets Manager. Se o `.env` não existir, ele pergunta os valores.

## Operação do dia a dia

| Ação | Comando |
|---|---|
| Publicar uma nova versão | `infra/scripts/deploy.sh` (a tag da imagem é o commit atual) |
| Coletar agora / reprocessar um período | `infra/scripts/run-collector.sh` ou `infra/scripts/run-collector.sh --from 2026-03-31` |
| Usuários | `infra/scripts/create-user.sh fulano`; `... fulano passwd\|disable\|enable\|delete`; `... - list` |
| Trocar as credenciais Dynatrace | `infra/scripts/set-dynatrace-secret.sh` (as novas tarefas usam o valor novo; no web: `deploy.sh`) |
| Logs | `aws logs tail /ecs/dyna-billing/web --follow` |
| Remover tudo | desligue `db_deletion_protection`, rode `deploy.sh` e depois `infra/scripts/destroy.sh` |

## Custo estimado (sa-east-1, uso contínuo)

Valores aproximados, que podem variar:

| Item | US$/mês |
|---|---|
| NAT Gateway | ~45 + tráfego |
| ALB | ~20 |
| RDS db.t4g.micro + 20 GB gp3 | ~25 |
| Fargate web (0,25 vCPU / 0,5 GB, ARM) | ~9 |
| Fargate coletor (4 execuções de ~2 min/dia) | < 1 |
| Secrets Manager, CloudWatch, ECR | ~5 |
| **Total** | **~105** |

## Observações de segurança

- **HTTPS:** sem `acm_certificate_arn`, o ALB serve HTTP puro e a senha de login trafega em texto. Use HTTPS fora de testes.
- **Credenciais Dynatrace:** nunca passam pelo state do Terraform.
- **State do Terraform:** a senha do banco e a chave de sessão, geradas pelo Terraform, ficam no state. O bucket é privado, versionado e criptografado; restrinja o acesso a ele.
- **Usuário do banco:** a aplicação usa o usuário master do RDS. Para separar privilégios, crie um usuário só com DML no banco `dyna_billing`.
- **Bloqueio de login:** é feito em memória. Por isso o serviço web roda com 1 tarefa.
