# Companion code for "The Backend of Luck" - Chapter 28d, Elastic Compute and Cluster Autoscaling on EKS.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

# Real AWS provider. For local emulation (LocalStack / MiniStack) a
# local.override.tf is injected at test time that re-points the endpoints
# at :4566 — see ../README or scripts/chapter-29/.../run-terraform-tests.sh.
provider "aws" {
  region = var.aws_region
}
