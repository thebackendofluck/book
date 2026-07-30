# Companion code for "The Backend of Luck" - Chapter 28d, Elastic Compute and Cluster Autoscaling on EKS.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project / platform name used as a resource prefix"
  type        = string
  default     = "acmetocasino"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "staging"
}

variable "vpc_cidr" {
  description = "CIDR block for the EKS VPC"
  type        = string
  default     = "10.42.0.0/16"
}

variable "azs" {
  description = "Availability zones for multi-AZ node spread (Karpenter)"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "eks_cluster_version" {
  description = "EKS control-plane version"
  type        = string
  default     = "1.31"
}

variable "create_eks" {
  description = "Create the EKS control plane. Set false on emulators that do not implement the EKS API."
  type        = bool
  default     = true
}

variable "ecr_repositories" {
  description = "Service images published as multi-arch (amd64+arm64) manifest lists"
  type        = list(string)
  default     = ["casino-api", "bet-settlement", "ops-dashboard"]
}

variable "control_plane_log_retention_days" {
  description = "CloudWatch retention for EKS control-plane audit logs (PCI: >= 365)"
  type        = number
  default     = 365
}

variable "cluster_endpoint_public_access" {
  description = "Expose the EKS API publicly (CIS: prefer false / private-only)"
  type        = bool
  default     = false
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "If public access is on, restrict to these CIDRs (CIS)"
  type        = list(string)
  default     = ["10.0.0.0/8"]
}

variable "create_addons" {
  description = "Create managed EKS add-ons. Disable on emulators that do not implement the addon API."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Common tags"
  type        = map(string)
  default = {
    Platform   = "acmetocasino"
    ManagedBy  = "terraform"
    Compliance = "PCI-DSS"
  }
}
