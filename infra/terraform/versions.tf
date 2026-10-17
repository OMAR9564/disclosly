# Provider tokens come from the environment, never from files (research.md R9):
#   HCLOUD_TOKEN          Hetzner Cloud project token (read/write)
#   CLOUDFLARE_API_TOKEN  zone-scoped token for disclosly.com (DNS edit, zone settings edit)
# State is local and gitignored; the owner backs it up.

terraform {
  required_version = ">= 1.9"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.70"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.28"
    }
  }
}

provider "hcloud" {}

provider "cloudflare" {}
