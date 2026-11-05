terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {}

resource "docker_image" "nginx" {
  name         = var.image
  keep_locally = true
}

resource "docker_container" "nginx" {
  name  = var.container_name
  image = docker_image.nginx.image_id

  ports {
    internal = 80
    external = var.external_port
    ip       = "127.0.0.1" # only reachable from this machine
  }
}

output "url" {
  description = "URL where the nginx container is reachable."
  value       = "http://localhost:${var.external_port}"
}
