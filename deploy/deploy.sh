#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Bibliome — Production Deploy Script
# Target: Ubuntu/Debian server with nginx + PostgreSQL + Redis
# Domain: bibliome.app (served through a Cloudflare Tunnel)
#
# This script installs the checked-in config files under deploy/ rather than
# inlining its own copies. deploy/bibliome.nginx.conf and deploy/bibliome.service
# are the single source of truth — edit those, not this script.
# ─────────────────────────────────────────────────────────
set -euo pipefail

# ── Config ──
DOMAIN="bibliome.app"
APP_DIR="/srv/bibliome"
API_DIR="$APP_DIR/api"
FE_DIR="$APP_DIR/frontend"
API_PORT=8100
DB_NAME="bibliome"
DB_USER="bibliome"
SERVICE_NAME="bibliome"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ── Preflight checks ──
[[ $EUID -eq 0 ]] || error "Run as root: sudo bash deploy.sh"
command -v nginx    >/dev/null || error "nginx not installed"
command -v psql     >/dev/null || error "postgresql not installed"
command -v python3  >/dev/null || error "python3 not installed"
command -v node     >/dev/null || error "node not installed (needed for frontend build)"
command -v npm      >/dev/null || error "npm not installed"
command -v envsubst >/dev/null || error "envsubst not installed (apt install gettext-base)"
command -v redis-cli >/dev/null || warn "redis not installed — rate limiting will fall back to per-worker in-memory"

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║     Bibliome — Deploy Script     ║"
echo "  ║     $DOMAIN                 ║"
echo "  ╚══════════════════════════════════╝"
echo ""

# ─────────────────────────────────────────
# 1. Create directory structure
# ─────────────────────────────────────────
info "Creating directories..."
mkdir -p "$API_DIR" "$FE_DIR"

# ─────────────────────────────────────────
# 2. PostgreSQL setup
# ─────────────────────────────────────────
info "Setting up PostgreSQL..."
DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"
    info "Created DB user: $DB_USER"
else
    sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';"
    warn "DB user exists, password updated"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
    info "Created database: $DB_NAME"
else
    warn "Database $DB_NAME already exists"
fi

# ─────────────────────────────────────────
# 3. Backend setup
# ─────────────────────────────────────────
info "Setting up backend..."

# Clone or pull
if [ -d "$API_DIR/.git" ]; then
    cd "$API_DIR" && git pull origin main
    info "Backend updated from git"
else
    # If files exist but no git, back them up
    if [ -f "$API_DIR/app/main.py" ]; then
        warn "Existing API files found without git — backing up .env"
        [ -f "$API_DIR/.env" ] && cp "$API_DIR/.env" /tmp/bibliome-env-backup
    fi
    git clone https://github.com/shrutipandey15/bibliome.git "$API_DIR"
    [ -f /tmp/bibliome-env-backup ] && mv /tmp/bibliome-env-backup "$API_DIR/.env"
    info "Backend cloned"
fi

cd "$API_DIR"

# Python venv
if [ ! -d "venv" ]; then
    python3 -m venv venv
    info "Created Python venv"
fi
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
info "Python dependencies installed"

# Generate .env if missing
SECRET=$(openssl rand -hex 32)
GIT_SHA=$(git -C "$API_DIR" rev-parse --short HEAD)
if [ ! -f .env ]; then
    cat > .env <<EOF
DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}
SECRET_KEY=${SECRET}
ENVIRONMENT=production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30
CORS_ORIGINS=https://${DOMAIN}
FRONTEND_URL=https://${DOMAIN}
TRUSTED_PROXY_COUNT=1
REDIS_URL=redis://localhost:6379/0
GOOGLE_BOOKS_API_KEY=
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USER=resend
SMTP_PASSWORD=
SMTP_FROM=noreply@${DOMAIN}
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
SQL_ECHO=0
GIT_SHA=${GIT_SHA}
EOF
    info "Generated .env with secure credentials"
    warn "SMTP_PASSWORD is empty — set your Resend key in $API_DIR/.env"
else
    # Update DB password and build SHA in the existing .env
    sed -i "s|postgresql+asyncpg://.*@localhost|postgresql+asyncpg://${DB_USER}:${DB_PASS}@localhost|" .env
    if grep -q '^GIT_SHA=' .env; then
        sed -i "s|^GIT_SHA=.*|GIT_SHA=${GIT_SHA}|" .env
    else
        echo "GIT_SHA=${GIT_SHA}" >> .env
    fi
    warn ".env exists — updated DB password and GIT_SHA"
fi

# Ensure www-data owns everything
chown -R www-data:www-data "$API_DIR"

# Run migrations
sudo -u www-data bash -c "cd $API_DIR && source venv/bin/activate && alembic upgrade head"
info "Database migrations applied"

deactivate

# ─────────────────────────────────────────
# 4. Frontend build
# ─────────────────────────────────────────
info "Building frontend..."

TEMP_FE=$(mktemp -d)
git clone https://github.com/shrutipandey15/bibliome-frontend.git "$TEMP_FE"
cd "$TEMP_FE"

npm ci --silent
# Same-origin: nginx serves the SPA and proxies /api/ to uvicorn.
VITE_API_URL="/api" npm run build

# Deploy built files
rm -rf "$FE_DIR"/*
cp -r dist/* "$FE_DIR/"
chown -R www-data:www-data "$FE_DIR"
rm -rf "$TEMP_FE"
info "Frontend built and deployed to $FE_DIR"

# ─────────────────────────────────────────
# 5. Systemd service (from deploy/bibliome.service)
# ─────────────────────────────────────────
info "Installing systemd service..."

cp "$API_DIR/deploy/bibliome.service" "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
info "Service $SERVICE_NAME started"

# ─────────────────────────────────────────
# 6. Nginx config (from deploy/bibliome.nginx.conf)
# ─────────────────────────────────────────
info "Configuring nginx..."

# Substitute ONLY our three placeholders — everything else ($uri, $host,
# $remote_addr, …) is an nginx runtime variable and must pass through untouched.
DOMAIN="$DOMAIN" FE_DIR="$FE_DIR" API_PORT="$API_PORT" \
    envsubst '${DOMAIN} ${FE_DIR} ${API_PORT}' \
    < "$API_DIR/deploy/bibliome.nginx.conf" \
    > "/etc/nginx/sites-available/${SERVICE_NAME}"

ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" /etc/nginx/sites-enabled/
nginx -t || error "nginx config test failed"
systemctl reload nginx
info "Nginx configured and reloaded"

# ─────────────────────────────────────────
# 7. TLS
# ─────────────────────────────────────────
# Nothing to do. TLS terminates at Cloudflare; cloudflared connects to
# http://localhost:80. Do NOT run certbot — there is no public :443 to validate
# against, and .app is HSTS-preloaded so the edge cert covers the browser side.

# ─────────────────────────────────────────
# 8. Verify
# ─────────────────────────────────────────
echo ""
sleep 2
if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    info "API health check passed (process + database)"
else
    warn "API not healthy — check: journalctl -u $SERVICE_NAME -f"
fi

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║          Deploy complete!                    ║"
echo "  ║                                              ║"
echo "  ║  Site: https://${DOMAIN}                     ║"
echo "  ║                                              ║"
echo "  ║  Useful commands:                            ║"
echo "  ║  journalctl -u bibliome -f     (API logs)    ║"
echo "  ║  systemctl restart bibliome    (restart)     ║"
echo "  ║  sudo bash deploy.sh           (redeploy)    ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  Next: confirm the tunnel is up, then verify the real client IP —"
echo "    curl -s https://${DOMAIN}/api/meta/version"
echo "    journalctl -u ${SERVICE_NAME} | grep -i 'rate'   # should show real IPs, not 127.0.0.1"
echo ""
