# ---------------------------------------------------------------------------------------------------------------------
# Packer settings
# ---------------------------------------------------------------------------------------------------------------------

packer {
  required_plugins {
    amazon = {
      version = ">=v1.0.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

# ---------------------------------------------------------------------------------------------------------------------
# AMI lookup
# ---------------------------------------------------------------------------------------------------------------------

data "amazon-ami" "ubuntu" {
  filters = {
    architecture                       = "x86_64"
    "block-device-mapping.volume-type" = "gp2"
    name                               = var.ubuntu_image
    root-device-type                   = "ebs"
    virtualization-type                = "hvm"
  }

  assume_role {
    role_arn     = "arn:aws:iam::111222333444:role/imageengineering_svc_role"
    session_name = "imageengineering_svc_role"
  }

  most_recent = true
  owners      = var.ami_owners
  region      = var.aws_region
}

# ---------------------------------------------------------------------------------------------------------------------
# Reusable source configuration
# ---------------------------------------------------------------------------------------------------------------------

source "amazon-ebs" "machine_image" {
  ami_description             = "Ubuntu AMI: ${var.ubuntu_image}"
  ami_name                    = "${var.ami_name}-${var.version_tag}-${formatdate("YYYYMMDD-hhmm", timestamp())}"
  ami_regions                 = var.copy_to_regions
  region_kms_key_ids          = var.region_kms_key_ids
  ami_users                   = var.ami_users
  associate_public_ip_address = var.associate_public_ip_address
  availability_zone           = var.availability_zone
  encrypt_boot                = var.encrypt_boot
  kms_key_id                  = var.encrypt_kms_key_id
  instance_type               = var.instance_type
  region                      = var.aws_region
  source_ami                  = data.amazon-ami.ubuntu.id
  ssh_username                = "ubuntu"
  ssh_interface               = var.ssh_interface
  tags = {
    version = var.version_tag
    status  = "approved"
    owner   = "acmetocasino"
  }

  assume_role {
    role_arn     = "arn:aws:iam::111222333444:role/imageengineering_svc_role"
    session_name = "imageengineering_svc_role"
  }

  dynamic "vpc_filter" {
    for_each = var.vpc_filter_key != "" ? ["once"] : []
    content {
      filters = {
        (var.vpc_filter_key) = var.vpc_filter_value
      }
    }
  }
  dynamic "subnet_filter" {
    for_each = var.vpc_subnet_filter_key != "" ? ["once"] : []
    content {
      filters = {
        (var.vpc_subnet_filter_key) = var.vpc_subnet_filter_value
      }
      most_free = "true"
    }
  }
}

# ---------------------------------------------------------------------------------------------------------------------
# Builders and provisioners
# ---------------------------------------------------------------------------------------------------------------------

build {
  sources = [
    "source.amazon-ebs.machine_image"
  ]

  provisioner "shell" {
    inline = ["echo Building ${var.ubuntu_image} EC2 AMI."]
  }

  provisioner "shell" {
    execute_command = "echo 'packer' | sudo -S sh -c '{{ .Vars }} {{ .Path }}'"

    inline = [
      "mkdir -p /packer/build",
      "chmod -R 777 /packer/build"
    ]
  }

  provisioner "file" {
    destination = "/packer/build"
    source      = "${path.root}/"
  }

  provisioner "shell" {
    environment_vars = [
      "RELEASE=${var.release}",
      "DD_AGENT_MAJOR_VERSION=${var.dd_agent_version}",
      "DD_SITE=${var.dd_site}",
      "DD_API_KEY=${var.dd_api_key}",
      "DD_INSTALL_ONLY=true"
    ]

    execute_command = "echo 'packer' | sudo -S sh -c '{{ .Vars }} {{ .Path }}'"

    inline = [
      "echo Running ${var.ubuntu_image} EC2 AMI Hardening script",
      "/bin/bash /packer/build/install.sh",
    ]
    pause_before = "15s"
  }

  post-processor "manifest" {
    output     = "manifest.json"
    strip_path = true
    custom_data = {
      release = var.release
    }
  }

}
