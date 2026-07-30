
# ---------------------------------------------------------------------------------------------------------------------
# REQUIRED inputs
# ---------------------------------------------------------------------------------------------------------------------

variable "version_tag" {
  description = "The value to use for the version tag that is created on the resulting AMI."
  type        = string
  default     = "Ubuntu20"
}

variable "ubuntu_image" {
  type = string
}

variable "release" {
  type = string
}

variable "ami_users" {
  description = "A list of account IDs that can access the AMI."
  type        = list(string)
}

variable "dd_api_key" {
  description = "Datadog API key for agent installation"
  type        = string
}

# ---------------------------------------------------------------------------------------------------------------------
# OPTIONAL inputs
# ---------------------------------------------------------------------------------------------------------------------
variable "dd_agent_version" {
  description = "Datadog agent major version"
  type        = string
  default     = "7"
}

variable "dd_site" {
  description = "Datadog site"
  type        = string
  default     = "datadoghq.com"
}

variable "ami_owners" {
  description = "The owners of the base AMI"
  type        = list(string)
  default     = ["099720109477"]
}

variable "ami_name" {
  description = "The name to apply to the AMI. A timestamp is appended for uniqueness."
  type        = string
  default     = "AcmeCorpUbuntu20"
}

variable "associate_public_ip_address" {
  description = "If true, assign a public IP address to the AMI builder instance."
  type        = string
  default     = "true"
}

variable "availability_zone" {
  description = "The Availability Zone in which to launch the instance."
  type        = string
  default     = "us-east-1a"
}

variable "aws_region" {
  description = "The region in which to search for the source AMI and build the new AMI."
  type        = string
  default     = "us-east-1"
}

variable "copy_to_regions" {
  description = "Additional regions the AMI should be copied to."
  type        = list(string)
  default     = ["eu-west-1", "eu-west-2", "ca-central-1"]
}

variable "region_kms_key_ids" {
  description = "Map of regions to KMS key for AMI encryption."
  type        = map(string)
  default = {
    "eu-west-1"    = "arn:aws:kms:eu-west-1:111222333444:alias/ebs-encryption",
    "eu-west-2"    = "arn:aws:kms:eu-west-2:111222333444:alias/ebs-encryption",
    "ca-central-1" = "arn:aws:kms:ca-central-1:111222333444:alias/ebs-encryption"
  }
}

variable "encrypt_boot" {
  description = "Whether to encrypt the AMI boot disk."
  type        = string
  default     = "true"
}

variable "encrypt_kms_key_id" {
  description = "The KMS key ID for encrypting the boot disk."
  type        = string
  default     = "arn:aws:kms:us-east-1:111222333444:alias/ebs-encryption"
}

variable "ssh_interface" {
  description = "Which interface to use for SSH during provisioning."
  type        = string
  default     = "public_ip"
}

variable "vpc_filter_key" {
  description = "Tag key for filtering which VPC to build in."
  type        = string
  default     = "tag:Name"
}

variable "vpc_filter_value" {
  type    = string
  default = "app"
}

variable "vpc_subnet_filter_key" {
  type    = string
  default = "tag:Name"
}

variable "vpc_subnet_filter_value" {
  type    = string
  default = "app-public-0"
}

variable "instance_type" {
  description = "Instance type for the build instance."
  type        = string
  default     = "t2.large"
}
