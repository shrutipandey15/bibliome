#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Book DNA — Quick Update (pull latest code + rebuild)
# Run after pushing changes to GitHub
# ─────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/srv/bookdna"
API_DIR="$APP_DIR/api"
FE_DIR="$APP_DIR/frontend"
SERVICE_NAME="bookdna"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[✓]${NC} $1"; }

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash update.sh"; exit 1; }

echo "Updating Book DNA..."

# Backend
cd "$API_DIR"
sudo -u www-data git pull origin main
source venv/bin/activate
pip install --quiet -r requirements.txt
sudo -u www-data bash -c "cd $API_DIR && source venv/bin/activate && alembic upgrade head"
deactivate
systemctl restart "$SERVICE_NAME"
info "Backend updated & restarted"

# Frontend
TEMP_FE=$(mktemp -d)
git clone --depth 1 https://github.com/shrutipandey15/bookDNA-frontend.git "$TEMP_FE"
cd "$TEMP_FE"
npm ci --silent
VITE_API_URL="/api" npm run build
rm -rf "$FE_DIR"/*
cp -r dist/* "$FE_DIR/"
chown -R www-data:www-data "$FE_DIR"
rm -rf "$TEMP_FE"
info "Frontend rebuilt"

echo ""
info "Done! Site live at https://bookdna.fdev31.space"
