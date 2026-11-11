variable "aws_region" {
  description = "Região AWS"
  type        = string
  default     = "sa-east-1"
}

variable "project" {
  description = "Prefixo dos recursos"
  type        = string
  default     = "dyna-billing"
}

variable "tags" {
  description = "Tags adicionais"
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------ rede

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "allowed_cidrs" {
  description = "CIDRs que podem acessar a interface web (ex.: rede corporativa / VPN). Evite 0.0.0.0/0."
  type        = list(string)
}

# ------------------------------------------------------------------ HTTPS

variable "acm_certificate_arn" {
  description = "ARN de um certificado ACM (na mesma região) para HTTPS no ALB. Vazio = somente HTTP (não recomendado)."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Opcional: zona Route53 para criar o registro DNS da aplicação"
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Opcional: nome DNS da aplicação (ex.: billing.exemplo.com.br), usado com route53_zone_id"
  type        = string
  default     = ""
}

# ------------------------------------------------------------------ aplicação

variable "image_tag" {
  description = "Tag da imagem no ECR (definida pelo scripts/deploy.sh)"
  type        = string
  default     = "latest"
}

variable "web_cpu" {
  type    = number
  default = 256
}

variable "web_memory" {
  type    = number
  default = 512
}

variable "collector_cpu" {
  type    = number
  default = 256
}

variable "collector_memory" {
  type    = number
  default = 512
}

variable "collector_schedule" {
  description = "Expressão do EventBridge Scheduler para o coletor"
  type        = string
  default     = "rate(6 hours)"
}

variable "app_env" {
  description = <<-EOT
    Variáveis de ambiente NÃO sensíveis da aplicação (ver .env.example), ex.:
    DT_ACCOUNT_UUID, DT_SUBSCRIPTION_UUID, CONTRACT_END_DATE, CONTRACT_START_DATE,
    CONTRACT_TOTAL_CREDITS, AVG_WINDOW_DAYS, FX_MODE, REFRESH_DAYS.
  EOT
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  type    = number
  default = 90
}

# ------------------------------------------------------------------ banco

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "db_deletion_protection" {
  description = "Impede destroy acidental do RDS. Desligue explicitamente antes de destruir o ambiente."
  type        = bool
  default     = true
}
