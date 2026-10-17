# Hetzner server, firewall and SSH keys, plus Cloudflare DNS and TLS settings (research.md R8, R9).

# --- Hetzner ---

resource "hcloud_ssh_key" "maintainer" {
  count      = length(var.ssh_public_keys)
  name       = "disclosly-maintainer-${count.index}"
  public_key = var.ssh_public_keys[count.index]
}

resource "hcloud_firewall" "api" {
  name = "disclosly-api"

  dynamic "rule" {
    for_each = ["22", "80", "443"]
    content {
      direction  = "in"
      protocol   = "tcp"
      port       = rule.value
      source_ips = ["0.0.0.0/0", "::/0"]
    }
  }
}

resource "hcloud_server" "api" {
  name         = "disclosly-api"
  server_type  = var.server_type
  location     = var.location
  image        = var.image
  ssh_keys     = hcloud_ssh_key.maintainer[*].id
  firewall_ids = [hcloud_firewall.api.id]

  # The deploy user's keys are public, so passing them in user_data is safe.
  user_data = templatefile("${path.module}/cloud-init.yaml", {
    deploy_authorized_keys = concat(var.ssh_public_keys, [var.deploy_ssh_public_key])
  })

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  lifecycle {
    # cloud-init runs only on first boot; editing it must not silently rebuild the server.
    ignore_changes = [user_data, ssh_keys]
  }
}

# --- Cloudflare ---

data "cloudflare_zone" "main" {
  filter = {
    name = var.domain
  }
}

resource "cloudflare_dns_record" "api" {
  zone_id = data.cloudflare_zone.main.zone_id
  name    = "api.${var.domain}"
  type    = "A"
  content = hcloud_server.api.ipv4_address
  proxied = true
  ttl     = 1
}

resource "cloudflare_dns_record" "apex" {
  zone_id = data.cloudflare_zone.main.zone_id
  name    = var.domain
  type    = "A"
  content = var.vercel_apex_ipv4
  proxied = false
  ttl     = 300
}

resource "cloudflare_dns_record" "www" {
  zone_id = data.cloudflare_zone.main.zone_id
  name    = "www.${var.domain}"
  type    = "CNAME"
  content = var.vercel_www_cname
  proxied = false
  ttl     = 300
}

# Full (strict): Cloudflare verifies Caddy's Let's Encrypt certificate. Caddy, not Cloudflare,
# redirects HTTP to HTTPS, so the HTTP-01 challenge can reach it.
resource "cloudflare_zone_setting" "ssl" {
  zone_id    = data.cloudflare_zone.main.zone_id
  setting_id = "ssl"
  value      = "strict"
}

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = data.cloudflare_zone.main.zone_id
  setting_id = "always_use_https"
  value      = "off"
}

resource "cloudflare_zone_setting" "min_tls_version" {
  zone_id    = data.cloudflare_zone.main.zone_id
  setting_id = "min_tls_version"
  value      = "1.2"
}
