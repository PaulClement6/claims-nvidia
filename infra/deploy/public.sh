#!/usr/bin/env bash
# The public door of the demo (issue #65): Caddy with automatic HTTPS and a
# password, in front of the frontend on 127.0.0.1:5173. Nothing else is public.
# Run it again to repair it. To change the password: delete the password file
# and run it again. To close the door: sudo systemctl disable --now caddy
set -euo pipefail

user="demo"
pwfile="$HOME/.config/claims/demo-password"
# ponytail: sslip.io turns the public IP into a host name, so no domain to buy.
# The URL changes if the IP changes. Set PUBLIC_HOST when you have a real name.
host="${PUBLIC_HOST:-$(curl -fsS https://api.ipify.org | tr . -).sslip.io}"

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
$host {
	basic_auth {
		$user $hash
	}
	reverse_proxy 127.0.0.1:5173
}
EOF
sudo systemctl enable caddy
sudo systemctl restart caddy

echo "URL:      https://$host"
echo "User:     $user"
echo "Password: cat $pwfile"
echo "Ports 80 and 443 must be open on the VM (Brev console, Access, Expose port)."
