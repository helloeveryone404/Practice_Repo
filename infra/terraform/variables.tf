variable "image" {
  type        = string
  description = "Docker image for the nginx container, pinned to a specific alpine tag."
  default     = "nginx:1.30.6-alpine"
}

variable "container_name" {
  type        = string
  description = "Name of the nginx container Terraform creates."
  default     = "tf-practice-nginx"
}

variable "external_port" {
  type        = number
  description = "Port on this machine that maps to nginx's port 80 inside the container."
  default     = 8080
}
