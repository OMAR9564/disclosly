variable "server_type" {
  description = "Hetzner shared-vCPU type with 2 vCPU / 4 GB RAM / 40 GB disk. Check the current name (`hcloud server-type list`) before apply."
  type        = string
  default     = "cx23"
}

variable "location" {
  description = "Hetzner location."
  type        = string
  default     = "fsn1"
}

variable "image" {
  description = "Server OS image."
  type        = string
  default     = "ubuntu-24.04"
}

variable "ssh_public_keys" {
  description = "Maintainer SSH public keys. Registered in Hetzner and authorized for the deploy user."
  type        = list(string)

  validation {
    condition     = length(var.ssh_public_keys) > 0
    error_message = "Provide at least one maintainer SSH public key."
  }
}

variable "deploy_ssh_public_key" {
  description = "Public half of the CI deploy key (the private half is the DEPLOY_SSH_KEY GitHub secret)."
  type        = string
}

variable "domain" {
  description = "Cloudflare zone name."
  type        = string
  default     = "disclosly.com"
}

variable "vercel_apex_ipv4" {
  description = "Vercel A record target for the apex domain."
  type        = string
  default     = "76.76.21.21"
}

variable "vercel_www_cname" {
  description = "Vercel CNAME target for www."
  type        = string
  default     = "cname.vercel-dns.com"
}
