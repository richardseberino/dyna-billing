terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Configuração parcial: bucket/tabela são passados pelo scripts/tf.sh (backend.hcl),
  # criados uma única vez pelo scripts/bootstrap-state.sh.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge({
      Project   = var.project
      ManagedBy = "terraform"
    }, var.tags)
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}
