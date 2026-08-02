#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Bibliome — Quick Update (pull latest code + rebuild)
# Run after pushing changes to GitHub.
#
# This does NOT reinstall nginx/systemd config. If you changed
# deploy/bibliome.nginx.conf or deploy/bibliome.service, run deploy.sh instead.
# ─────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/srv/bibliome"
API_DIR="$APP_DIR/api"
FE_DIR="$APP_DIR/frontend"
SERVICE_NAME="bibliome"
DOMAIN="bibliome.app"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash update.sh"; exit 1; }

echo "Updating Bibliome..."

# Backend
cd "$API_DIR"
sudo -u www-data git pull origin main
source venv/bin/activate
pip install --quiet -r requirements.txt
sudo -u www-data bash -c "cd $API_DIR && source venv/bin/activate && alembic upgrade head"
deactivate

# Stamp the running build so /api/meta/version tells the truth.
GIT_SHA=$(git -C "$API_DIR" rev-parse --short HEAD)
if grep -q '^GIT_SHA=' "$API_DIR/.env"; then
    sed -i "s|^GIT_SHA=.*|GIT_SHA=${GIT_SHA}|" "$API_DIR/.env"
else
    echo "GIT_SHA=${GIT_SHA}" >> "$API_DIR/.env"
fi

systemctl restart "$SERVICE_NAME"
info "Backend updated & restarted (${GIT_SHA})"

# Frontend
TEMP_FE=$(mktemp -d)
git clone --depth 1 https://github.com/shrutipandey15/bibliome-frontend.git "$TEMP_FE"
cd "$TEMP_FE"
npm ci --silent
# Same-origin: nginx serves the SPA and proxies /api/ to uvicorn.
VITE_API_URL="/api" npm run build
rm -rf "$FE_DIR"/*
cp -r dist/* "$FE_DIR/"
chown -R www-data:www-data "$FE_DIR"
rm -rf "$TEMP_FE"
info "Frontend rebuilt"

echo ""
info "Done! Site live at https://${DOMAIN}"
