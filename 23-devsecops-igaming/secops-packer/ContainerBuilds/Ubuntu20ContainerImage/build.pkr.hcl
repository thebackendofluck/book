packer {
  required_plugins {
    docker = {
      version = ">= 0.0.7"
      source  = "github.com/hashicorp/docker"
    }
  }
}

source "docker" "ubuntu" {
  image  = var.ubuntu_image
  commit = true
  changes = [
    "EXPOSE 8080",
    "LABEL version=${var.release}",
    "ENTRYPOINT [\"/bin/bash\", \"/packer/build/ubuntu.sh\"]"
  ]
}

build {
  name = "build-packer"
  sources = [
    "source.docker.ubuntu"
  ]

  provisioner "shell" {
    inline = ["echo Running ${var.ubuntu_image} Docker image."]
  }

  provisioner "shell" {
    inline = ["mkdir -p /packer/build"]
  }

  provisioner "file" {
    destination = "/packer/build"
    source      = "${path.root}/"
  }

  provisioner "shell" {
    environment_vars = [
      "RELEASE=${var.release}",
    ]
    inline = [
      "echo Running ${var.ubuntu_image} Docker Container",
      "chmod -R 777 /packer/build",
      "/bin/bash /packer/build/ubuntu.sh",
    ]
  }

  post-processors {
    post-processor "docker-tag" {
      repository = "111222333444.dkr.ecr.eu-west-1.amazonaws.com/corp/ubuntu20"
      tags       = ["latest", var.release]
      only       = ["docker.ubuntu"]
    }

    post-processor "docker-push" {
      ecr_login    = true
      login_server = "https://111222333444.dkr.ecr.eu-west-1.amazonaws.com/"
    }
  }
}
