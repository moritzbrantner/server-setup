# server-setup

Scripts for managing a Hetzner Ubuntu LTS server running multiple websites/services.

## Scripts

### 1) Install Nginx site config for a domain

```bash
sudo ./scripts/install-nginx-site.sh \
  --domain example.com \
  --root /var/www/example.com/public \
  --www-redirect
```

What it does:
- Installs Nginx
- Creates the web root
- Writes `/etc/nginx/sites-available/example.com.conf`
- Enables the site and reloads Nginx
- Opens `Nginx Full` in UFW (if active)

### 2) Configure Let's Encrypt certificate (Certbot)

```bash
sudo ./scripts/setup-letsencrypt.sh \
  --domain example.com \
  --email admin@example.com \
  --www
```

What it does:
- Installs Certbot + Nginx plugin
- Requests a certificate with HTTP->HTTPS redirect
- Enables renewal timer
- Runs `certbot renew --dry-run`

## Notes

- Point DNS A/AAAA records to your Hetzner server before running Let's Encrypt.
- Ensure ports `80` and `443` are reachable.
