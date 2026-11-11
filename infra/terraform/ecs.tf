locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  common_env = merge(
    {
      DB_HOST               = aws_db_instance.main.address
      DB_PORT               = tostring(aws_db_instance.main.port)
      DB_NAME               = aws_db_instance.main.db_name
      DB_USER               = aws_db_instance.main.username
      DT_MOCK               = "false"
      SESSION_COOKIE_SECURE = local.https_enabled ? "true" : "false"
      TZ                    = "America/Sao_Paulo"
    },
    var.app_env,
  )

  env_list = [for k, v in local.common_env : { name = k, value = v }]

  secrets = [
    { name = "DB_PASSWORD", valueFrom = aws_secretsmanager_secret.db_password.arn },
    { name = "SESSION_SECRET", valueFrom = aws_secretsmanager_secret.session.arn },
    { name = "DT_CLIENT_ID", valueFrom = "${aws_secretsmanager_secret.dynatrace.arn}:DT_CLIENT_ID::" },
    { name = "DT_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.dynatrace.arn}:DT_CLIENT_SECRET::" },
    { name = "DT_BEARER_TOKEN", valueFrom = "${aws_secretsmanager_secret.dynatrace.arn}:DT_BEARER_TOKEN::" },
  ]

  runtime_platform = {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64" # imagem construída em arm64 (Mac Apple Silicon); Graviton é mais barato
  }
}

resource "aws_ecs_cluster" "main" {
  name = var.project
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${var.project}/web"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "collector" {
  name              = "/ecs/${var.project}/collector"
  retention_in_days = var.log_retention_days
}

# ------------------------------------------------------------------ IAM

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execução: puxar imagem, gravar logs, ler os segredos
resource "aws_iam_role" "execution" {
  name               = "${var.project}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.db_password.arn,
        aws_secretsmanager_secret.session.arn,
        aws_secretsmanager_secret.dynatrace.arn,
      ]
    }]
  })
}

# Tarefa: apenas ECS Exec (para criar usuários via scripts/create-user.sh)
resource "aws_iam_role" "task" {
  name               = "${var.project}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task_exec" {
  name = "ecs-exec"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
      ]
      Resource = "*"
    }]
  })
}

# ------------------------------------------------------------------ web

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.project}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = local.runtime_platform.operating_system_family
    cpu_architecture        = local.runtime_platform.cpu_architecture
  }

  container_definitions = jsonencode([{
    name      = "web"
    image     = local.image
    essential = true
    # proxy-headers: usa o IP real do cliente (X-Forwarded-For do ALB) no bloqueio de login
    command         = ["uvicorn", "dynabilling.web:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
    portMappings    = [{ containerPort = 8000, protocol = "tcp" }]
    environment     = local.env_list
    secrets         = local.secrets
    linuxParameters = { initProcessEnabled = true } # necessário para ECS Exec
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.web.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "web"
      }
    }
  }])
}

resource "aws_ecs_service" "web" {
  name                   = "web"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.web.arn
  desired_count          = 1 # o bloqueio de login é em memória: mantenha 1 tarefa
  launch_type            = "FARGATE"
  enable_execute_command = true
  propagate_tags         = "SERVICE"

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}

# ------------------------------------------------------------------ coletor (tarefa agendada)

resource "aws_ecs_task_definition" "collector" {
  family                   = "${var.project}-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.collector_cpu
  memory                   = var.collector_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = local.runtime_platform.operating_system_family
    cpu_architecture        = local.runtime_platform.cpu_architecture
  }

  container_definitions = jsonencode([{
    name        = "collector"
    image       = local.image
    essential   = true
    command     = ["python", "-m", "dynabilling.collector", "--once"]
    environment = local.env_list
    secrets     = local.secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.collector.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "collector"
      }
    }
  }])
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "run-collector"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecs:RunTask"]
        Resource  = ["${aws_ecs_task_definition.collector.arn_without_revision}:*"]
        Condition = { ArnLike = { "ecs:cluster" = aws_ecs_cluster.main.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
      },
    ]
  })
}

resource "aws_scheduler_schedule" "collector" {
  name                         = "${var.project}-collector"
  schedule_expression          = var.collector_schedule
  schedule_expression_timezone = "America/Sao_Paulo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.collector.arn_without_revision
      launch_type         = "FARGATE"
      task_count          = 1
      network_configuration {
        subnets          = aws_subnet.private[*].id
        security_groups  = [aws_security_group.app.id]
        assign_public_ip = false
      }
    }

    retry_policy {
      maximum_retry_attempts = 2
    }
  }
}
