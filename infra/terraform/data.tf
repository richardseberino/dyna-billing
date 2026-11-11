# ------------------------------------------------------------------ ECR

resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Mantém as 20 imagens mais recentes"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# ------------------------------------------------------------------ RDS MySQL

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = var.project
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "mysql" {
  name   = "${var.project}-mysql84"
  family = "mysql8.4"
  parameter {
    name  = "time_zone"
    value = "Brazil/East" # = America/Sao_Paulo; o RDS só aceita este nome
  }
  parameter {
    name  = "require_secure_transport"
    value = "0"
  }
}

resource "aws_db_instance" "main" {
  identifier                 = var.project
  engine                     = "mysql"
  engine_version             = "8.4"
  auto_minor_version_upgrade = true
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  max_allocated_storage      = var.db_allocated_storage * 5
  storage_type               = "gp3"
  storage_encrypted          = true

  db_name  = "dyna_billing"
  username = "dyna"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.mysql.name
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "06:00-07:00"         # 03h-04h em São Paulo
  maintenance_window        = "sun:07:00-sun:08:00" # dom 04h-05h em São Paulo
  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project}-final"
  copy_tags_to_snapshot     = true

  performance_insights_enabled = false
}

# ------------------------------------------------------------------ Secrets Manager

# Senha do banco e chave de sessão: geradas pelo Terraform.
resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${var.project}/db-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db.result
}

resource "random_password" "session" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "session" {
  name                    = "${var.project}/session-secret"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "session" {
  secret_id     = aws_secretsmanager_secret.session.id
  secret_string = random_password.session.result
}

# Credenciais Dynatrace: o Terraform cria só o "contêiner" com um valor provisório;
# o valor real é gravado pelo scripts/set-dynatrace-secret.sh e NÃO passa pelo state.
resource "aws_secretsmanager_secret" "dynatrace" {
  name                    = "${var.project}/dynatrace"
  description             = "JSON {DT_CLIENT_ID, DT_CLIENT_SECRET} ou {DT_BEARER_TOKEN}"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "dynatrace_placeholder" {
  secret_id     = aws_secretsmanager_secret.dynatrace.id
  secret_string = jsonencode({ DT_CLIENT_ID = "", DT_CLIENT_SECRET = "", DT_BEARER_TOKEN = "" })
  lifecycle {
    ignore_changes = [secret_string, version_stages]
  }
}
