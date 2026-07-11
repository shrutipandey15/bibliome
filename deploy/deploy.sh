#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Book DNA — Production Deploy Script
# Target: Ubuntu/Debian server with nginx + PostgreSQL
# Domain: bookdna.fdev31.space
# ─────────────────────────────────────────────────────────
set -euo pipefail

# ── Config ──
DOMAIN="bookdna.fdev31.space"
APP_DIR="/srv/bookdna"
API_DIR="$APP_DIR/api"
FE_DIR="$APP_DIR/frontend"
API_PORT=8100
DB_NAME="bookdna"
DB_USER="bookdna"
SERVICE_NAME="bookdna"

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

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║     Book DNA — Deploy Script     ║"
echo "  ║     $DOMAIN        ║"
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
        [ -f "$API_DIR/.env" ] && cp "$API_DIR/.env" /tmp/bookdna-env-backup
    fi
    git clone https://github.com/shrutipandey15/bookDNA.git "$API_DIR"
    [ -f /tmp/bookdna-env-backup ] && mv /tmp/bookdna-env-backup "$API_DIR/.env"
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

# Install system fonts for Pillow (OG image generation)
if ! dpkg -l | grep -q fonts-dejavu-core; then
    apt-get install -y --no-install-recommends fonts-dejavu-core fonts-liberation >/dev/null 2>&1
    info "Installed system fonts for image generation"
fi

# Generate .env if missing
SECRET=$(openssl rand -hex 32)
if [ ! -f .env ]; then
    cat > .env <<EOF
DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASS}@localhost:5432/${DB_NAME}
SECRET_KEY=${SECRET}
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7
CORS_ORIGINS=https://${DOMAIN}
ENVIRONMENT=production
EOF
    info "Generated .env with secure credentials"
else
    # Update DB password in existing .env
    sed -i "s|postgresql+asyncpg://.*@localhost|postgresql+asyncpg://${DB_USER}:${DB_PASS}@localhost|" .env
    warn ".env exists — updated DB password"
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
git clone https://github.com/shrutipandey15/bookDNA-frontend.git "$TEMP_FE"
cd "$TEMP_FE"

npm ci --silent
VITE_API_URL="/api" npm run build

# Deploy built files
rm -rf "$FE_DIR"/*
cp -r dist/* "$FE_DIR/"
chown -R www-data:www-data "$FE_DIR"
rm -rf "$TEMP_FE"
info "Frontend built and deployed to $FE_DIR"

# ─────────────────────────────────────────
# 5. Systemd service
# ─────────────────────────────────────────
info "Setting up systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Book DNA API
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=exec
User=www-data
Group=www-data
WorkingDirectory=${API_DIR}
EnvironmentFile=${API_DIR}/.env
ExecStart=${API_DIR}/venv/bin/uvicorn app.main:app \\
    --host 127.0.0.1 \\
    --port ${API_PORT} \\
    --workers 2 \\
    --log-level info
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${API_DIR}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
info "Service $SERVICE_NAME started"

# ─────────────────────────────────────────
# 6. Nginx config
# ─────────────────────────────────────────
info "Configuring nginx..."

cat > /etc/nginx/sites-available/bookdna <<NGINX
server {
    server_name ${DOMAIN};

    # Frontend: static SPA
    location / {
        root ${FE_DIR};
        index index.html;
        try_files \$uri \$uri/ /index.html;

        location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
            expires 30d;
            add_header Cache-Control "public, immutable";
        }
    }

    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:${API_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
    }

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:${API_PORT};
    }

    # Gzip
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml image/svg+xml;
    gzip_min_length 1000;

    listen 80;
}
NGINX

# Enable site
ln -sf /etc/nginx/sites-available/bookdna /etc/nginx/sites-enabled/
nginx -t || error "nginx config test failed"
systemctl reload nginx
info "Nginx configured and reloaded"

# ─────────────────────────────────────────
# 7. TLS with Certbot
# ─────────────────────────────────────────
if command -v certbot >/dev/null; then
    info "Setting up HTTPS with Certbot..."
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
        --email "admin@fdev31.space" || warn "Certbot failed — run manually: sudo certbot --nginx -d $DOMAIN"
else
    warn "Certbot not installed. Install and run: sudo certbot --nginx -d $DOMAIN"
fi

# ─────────────────────────────────────────
# 8. Verify
# ─────────────────────────────────────────
echo ""
sleep 2
if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    info "API health check passed"
else
    warn "API not responding yet — check: journalctl -u $SERVICE_NAME -f"
fi

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║          Deploy complete!                ║"
echo "  ║                                          ║"
echo "  ║  Site: https://${DOMAIN}      ║"
echo "  ║                                          ║"
echo "  ║  Useful commands:                        ║"
echo "  ║  journalctl -u bookdna -f    (API logs)  ║"
echo "  ║  systemctl restart bookdna   (restart)   ║"
echo "  ║  sudo bash deploy.sh         (redeploy)  ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
