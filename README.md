# Dynatrace Billing

Coleta o billing diário da sua conta Dynatrace (Dynatrace Platform Subscription, DPS) **por ambiente e por recurso (capability)**, grava em MySQL e mostra tudo num dashboard web. O dashboard também diz se o seu gasto diário cabe no saldo de créditos até o fim do contrato, em **R$** e em **%**.

```
┌────────────┐   Account Management API   ┌────────────┐      ┌──────────┐
│ Dynatrace  │ ─────────────────────────▶ │ collector  │ ───▶ │  MySQL   │
└────────────┘   (Bearer token / OAuth)   └────────────┘      └────┬─────┘
                  PTAX (Banco Central) ──────────┘                 │
                                                  ┌────────────┐   │
                                   navegador ◀──  │ web :8000  │ ◀─┘
                                                  └────────────┘
```

## Início rápido

```bash
cp .env.example .env      # preencha DT_ACCOUNT_UUID e o token
docker compose up -d --build
docker compose exec web python -m dynabilling.auth add <seu-usuario>   # cria o primeiro login
open http://localhost:8000
```

Para testar sem credenciais, use `DT_MOCK=true` no `.env`. O coletor gera dados fictícios.

Para trocar de mock para dados reais, apague os dados fictícios antes:

```bash
docker compose down -v    # remove o volume do MySQL
# edite o .env: DT_MOCK=false + credenciais
docker compose up -d --build
```

## Credenciais Dynatrace

As APIs de billing da DPS fazem parte da **Account Management API** (`https://api.dynatrace.com`). Elas não aceitam um API token de ambiente (`dt0c01...`). Você tem duas opções:

| Opção | Variáveis | Permissões |
|---|---|---|
| OAuth client (recomendado; o token é renovado sozinho) | `DT_CLIENT_ID`, `DT_CLIENT_SECRET` | `account-uac-read` (uso e custo) e `account-env-read` (nomes dos ambientes) |
| Bearer token já emitido | `DT_BEARER_TOKEN` | as mesmas, mas o token expira e você precisa renová-lo |

Para criar o OAuth client: **Account Management → Identity & access management → OAuth clients → Create client**. O UUID da conta aparece no próprio client como `urn:dtaccount:<uuid>`. Use esse valor em `DT_ACCOUNT_UUID`.

### Endpoints usados

| Endpoint | Para quê |
|---|---|
| `GET /sub/v2/accounts/{acc}/subscriptions[/{sub}]` | Assinatura, período do contrato e budget |
| `GET /sub/v3/accounts/{acc}/subscriptions/{sub}/environments/cost` | Custo por ambiente e capability, consultado dia a dia |
| `GET /sub/v3/accounts/{acc}/subscriptions/{sub}/environments/usage` | Uso (quantidade e unidade) por ambiente e capability |
| `GET /env/v2/accounts/{acc}/environments` | Nomes dos ambientes |

## Coleta

- **Sempre até D-1.** O dia corrente nunca é coletado, porque a Dynatrace ainda não fechou o custo dele.
- **Primeira execução:** coleta desde o início do contrato. Esse histórico é necessário para calcular o consumido.
- **Execuções seguintes:** coleta a cada `COLLECT_INTERVAL_MINUTES` (padrão: 6 h) e reprocessa os últimos `REFRESH_DAYS` dias, porque a Dynatrace consolida os custos com atraso. A gravação é um upsert, então não há duplicidade.
- **Reprocessar manualmente:**
  ```bash
  docker compose run --rm collector python -m dynabilling.collector --once --from 2026-01-01 --to 2026-03-31
  ```
- Cada execução fica registrada em `collector_runs`.

## Avaliação de orçamento

```
saldo              = créditos totais − créditos consumidos
dias restantes     = dias entre o último dia completo coletado (D) e o fim do contrato
média permitida    = saldo ÷ dias restantes
% da média         = custo diário ÷ média permitida × 100
```

O dashboard compara a média permitida com:
- o custo do último dia completo;
- a **média dos últimos `AVG_WINDOW_DAYS` dias** (padrão: 3). É o valor em destaque e também a base da projeção e da data de esgotamento. Uma janela curta reflete o ritmo atual enquanto novas monitorações estão sendo incluídas; a média do contrato inteiro ficaria distorcida para baixo;
- as médias de 7 e 30 dias, como contexto de tendência.

Ele também mostra:
- a diferença em R$ por dia;
- o consumido (%) comparado ao tempo de contrato decorrido (%);
- a projeção do consumo no fim do contrato e a data em que os créditos acabam, no ritmo da média de referência.

Status:

| Status | Faixa |
|---|---|
| ✓ Dentro da média | até 100% |
| ! Acima da média | entre 100% e 110% |
| ✕ Acima da média | acima de 110% |

### De onde vêm os valores do contrato

| Dado | Fonte padrão | Sobrescrita no `.env` |
|---|---|---|
| Início e fim do contrato | API da assinatura | `CONTRACT_START_DATE`, `CONTRACT_END_DATE` |
| Créditos totais | API da assinatura | `CONTRACT_TOTAL_CREDITS` (na moeda do contrato) |
| Créditos consumidos | `budget.used` da API. Se a API não informar, ou se você definir `CONTRACT_TOTAL_CREDITS`, usa a soma dos custos diários coletados | – |

Se a API não retornar o total de créditos ou a data de fim (isso varia por tipo de contrato), defina esses valores no `.env`. O dashboard avisa quando falta algum deles.

### Conversão para Reais

- `FX_MODE=ptax` (padrão): o coletor busca a PTAX de venda do Banco Central e grava em `fx_rates`.
- `FX_MODE=fixed`: usa o valor de `FX_FIXED_RATE`.
- Contrato em BRL: a taxa é 1.

Todos os valores, inclusive o histórico, são convertidos pela cotação mais recente. Assim, o custo diário e a média permitida ficam na mesma base.

## Interface web e API

Em `http://localhost:8000`:
- resumo do orçamento e indicadores;
- custo diário empilhado por ambiente, com a linha da média permitida;
- custo por recurso;
- tabela ambiente × recurso;
- detalhe diário com exportação CSV.

Filtros disponíveis: período, ambientes e recursos.

| Rota | Descrição |
|---|---|
| `GET /api/summary` | Avaliação de orçamento (JSON) |
| `GET /api/daily?start&end&env&cap` | Série diária em R$ por ambiente |
| `GET /api/breakdown?...` | Totais por recurso, por ambiente e matriz |
| `GET /api/rows?...&limit` | Linhas detalhadas |
| `GET /api/export.csv?...` | CSV (separador `;`) |

## Autenticação da interface web

Todas as páginas e rotas `/api` exigem login. Só `/login` e `/health` são públicas.

- **Usuários:** ficam na tabela `users` do MySQL, com senha em hash `scrypt` (mínimo de 10 caracteres).
- **Sessão:** cookie assinado com `SESSION_SECRET`, `HttpOnly` e `SameSite=Strict`, que expira em `SESSION_MAX_AGE_HOURS` (padrão: 8 h).
- **Bloqueio:** 5 tentativas erradas bloqueiam o usuário e o IP por 15 minutos.
- **Efeito imediato:** um usuário bloqueado ou removido perde o acesso na próxima requisição.
- **HTTPS:** se a interface for servida por HTTPS (atrás de um proxy reverso), defina `SESSION_COOKIE_SECURE=true`.

Gestão de usuários:

```bash
docker compose exec web python -m dynabilling.auth add <usuario>       # cria (pede a senha)
docker compose exec web python -m dynabilling.auth passwd <usuario>    # troca a senha
docker compose exec web python -m dynabilling.auth disable <usuario>   # bloqueia o acesso
docker compose exec web python -m dynabilling.auth enable <usuario>    # libera de novo
docker compose exec web python -m dynabilling.auth delete <usuario>    # remove
docker compose exec web python -m dynabilling.auth list                # lista
```

## Banco de dados

O schema está em [app/dynabilling/schema.sql](app/dynabilling/schema.sql). A aplicação o aplica automaticamente ao iniciar, de forma idempotente, o que também funciona no RDS. A tabela principal é `daily_cost`, com uma linha por dia × ambiente × capability.

Para acessar o banco direto:

```bash
docker compose exec mysql mysql -udyna -p dyna_billing
```

## Testes

```bash
docker compose run --rm --no-deps web python -m pytest -q tests
```

## Deploy na AWS

Terraform e scripts em [infra/](infra/). O passo a passo está em [infra/README.md](infra/README.md).
