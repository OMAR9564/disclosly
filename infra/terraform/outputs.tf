output "server_ipv4" {
  description = "Public IPv4 of the API server (target of the proxied api A record)."
  value       = hcloud_server.api.ipv4_address
}
