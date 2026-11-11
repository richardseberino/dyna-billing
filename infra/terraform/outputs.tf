output "app_url" {
  description = "URL da interface web"
  value = (
    var.domain_name != "" ? "${local.https_enabled ? "https" : "http"}://${var.domain_name}" :
    "${local.https_enabled ? "https" : "http"}://${aws_lb.main.dns_name}"
  )
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "web_service" {
  value = aws_ecs_service.web.name
}

output "collector_task_definition" {
  value = aws_ecs_task_definition.collector.family
}

output "private_subnets" {
  value = join(",", aws_subnet.private[*].id)
}

output "app_security_group" {
  value = aws_security_group.app.id
}

output "dynatrace_secret_arn" {
  value = aws_secretsmanager_secret.dynatrace.arn
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "https_enabled" {
  value = local.https_enabled
}
