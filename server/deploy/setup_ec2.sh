#!/usr/bin/env bash
# One-shot bootstrap for the IBL feedback server on a fresh Ubuntu 22.04/24.04 host.
#
# Prerequisites:
#   - The repo (containing server/) is already on the box at REPO_DIR
#     (default /opt/ibl-feedback-server). Put it there via git clone or scp first.
#   - Your DNS name already points at this machine's public IP (needed for TLS).
#
# Usage (on the EC2 box):
#   sudo bash /opt/ibl-feedback-server/server/deploy/setup_ec2.sh <domain> <email>
#   e.g. sudo bash .../setup_ec2.sh agent-feedback.duckdns.org you@internationalbrainlab.org
#
# Re-runnable: safe to run again; it won't overwrite an existing secrets file.
set -euo pipefail

DOMAIN="${1:?usage: setup_ec2.sh <domain> <letsencrypt-email>}"
EMAIL="${2:?usage: setup_ec2.sh <domain> <letsencrypt-email>}"

REPO_DIR="${REPO_DIR:-/opt/ibl-feedback-server}"
SERVER_DIR="$REPO_DIR/server"
VENV="$REPO_DIR/.venv"
ENV_FILE="/etc/ibl-feedback.env"
DATA_DIR="/var/lib/ibl-feedback"
SERVICE_USER="iblfeedback"

if [ ! -d "$SERVER_DIR" ]; then
    echo "ERROR: $SERVER_DIR not found. Put the repo at $REPO_DIR first (git clone or scp)." >&2
    exit 1
fi

echo ">>> [1/8] Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx

echo ">>> [2/8] Creating service user and data directory..."
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --no-create-home "$SERVICE_USER"
mkdir -p "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"

echo ">>> [3/8] Building the virtualenv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$SERVER_DIR/requirements.txt"

echo ">>> [4/8] Writing $ENV_FILE (generated once; preserved on re-run)..."
if [ ! -f "$ENV_FILE" ]; then
    SECRET_KEY="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(50))')"
    INGEST_TOKEN="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    cat > "$ENV_FILE" <<EOF
IBL_FEEDBACK_SECRET_KEY=$SECRET_KEY
IBL_FEEDBACK_DEBUG=0
IBL_FEEDBACK_ALLOWED_HOSTS=$DOMAIN
IBL_FEEDBACK_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
IBL_FEEDBACK_INGEST_TOKEN=$INGEST_TOKEN
IBL_FEEDBACK_DB_PATH=$DATA_DIR/db.sqlite3
EOF
    echo ""
    echo "    *** SAVE THIS — clients need it as IBL_AGENT_FEEDBACK_TOKEN: ***"
    echo "    INGEST_TOKEN = $INGEST_TOKEN"
    echo ""
else
    echo "    $ENV_FILE already exists; leaving it untouched."
fi
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo ">>> [5/8] Running migrations (as $SERVICE_USER, so the DB file is writable)..."
sudo -u "$SERVICE_USER" bash -c "set -a; . '$ENV_FILE'; set +a; '$VENV/bin/python' '$SERVER_DIR/manage.py' migrate --noinput"

echo ">>> [6/8] Collecting static files..."
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
"$VENV/bin/python" "$SERVER_DIR/manage.py" collectstatic --noinput

echo ">>> [7/8] Installing systemd service + nginx site..."
install -m 644 "$SERVER_DIR/deploy/ibl-feedback.service" /etc/systemd/system/ibl-feedback.service
cat > /etc/nginx/sites-available/ibl-feedback <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 30m;
    location /static/ { alias $SERVER_DIR/staticfiles/; }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/ibl-feedback /etc/nginx/sites-enabled/ibl-feedback
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now ibl-feedback
systemctl restart nginx

echo ">>> [8/8] Requesting HTTPS certificate via Let's Encrypt..."
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect; then
    systemctl restart nginx
    echo ">>> Done. Site live at https://$DOMAIN/"
else
    echo "!!! certbot failed (usually DNS not yet pointing here). The site is up on http://$DOMAIN/."
    echo "!!! Once DNS resolves to this box, re-run: sudo certbot --nginx -d $DOMAIN --redirect"
fi

echo ""
echo "NEXT: create a reviewer login to view feedback in the browser:"
echo "  sudo -u $SERVICE_USER bash -c \"set -a; . $ENV_FILE; set +a; $VENV/bin/python $SERVER_DIR/manage.py createsuperuser\""
