#!/usr/bin/env bash
# The public door of the demo (issue #65): Caddy with a
# password, behind the HTTPS of Brev, in front of the frontend on 127.0.0.1:5173. Nothing else is public.
# Run it again to repair it. To change the password: delete the password file
# and run it again. To close the door: sudo systemctl disable --now caddy
set -euo pipefail

user="demo"
pwfile="$HOME/.config/claims/demo-password"
# Brev gives the HTTPS: an "HTTP port" of the Brev console sends
# https://80-<instance id>.gobrev.dev to port 80 of the VM. So Caddy speaks
# plain HTTP on 127.0.0.1:80 and asks for no certificate.

if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
    sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt |
    sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y caddy
fi

if [[ ! -s "$pwfile" ]]; then
  mkdir -p "$(dirname "$pwfile")"
  (umask 077 && openssl rand -base64 18 >"$pwfile")
fi
hash="$(caddy hash-password <"$pwfile")"

sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
http://:80 {
	bind 127.0.0.1
	basic_auth {
		$user $hash
	}
	reverse_proxy 127.0.0.1:5173
}
EOF
sudo systemctl enable caddy
sudo systemctl restart caddy

echo "User:     $user"
echo "Password: cat $pwfile"
echo "URL: Brev console, instance, Access, HTTP Ports: add port 80, then Share, public."
