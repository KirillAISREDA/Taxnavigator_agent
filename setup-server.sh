#!/bin/bash
# ============================================
# TaxNavigator Agent — Full Server Setup
# Run this ONCE on the server to set everything up
# ============================================

set -e

echo "🧭 TaxNavigator Agent — Server Setup"
echo "======================================"
echo ""

PROJECT_DIR="/opt/Taxnavigator_agent"

# -----------------------------------------------
# Step 1: Generate SSH key for GitHub
# -----------------------------------------------
echo "📋 Step 1: SSH key for GitHub"

if [ ! -f ~/.ssh/github_deploy ]; then
    ssh-keygen -t ed25519 -C "taxnav-deploy" -f ~/.ssh/github_deploy -N ""
    echo ""
    echo "✅ SSH key generated."
else
    echo "ℹ️  SSH key already exists at ~/.ssh/github_deploy"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "📎 COPY THIS PUBLIC KEY TO GITHUB:"
echo "═══════════════════════════════════════════"
echo ""
cat ~/.ssh/github_deploy.pub
echo ""
echo "═══════════════════════════════════════════"
echo "→ Go to: https://github.com/KirillAISREDA/Taxnavigator_agent/settings/keys/new"
echo "→ Paste the key above into the 'Key' field"
echo "→ Title: 'TaxNav Server'"
echo "→ Click 'Add key'"
echo ""
read -p "Press ENTER when you've added the key to GitHub..."

# -----------------------------------------------
# Step 2: SSH config
# -----------------------------------------------
echo ""
echo "📋 Step 2: SSH config"

if ! grep -q "github_deploy" ~/.ssh/config 2>/dev/null; then
    cat >> ~/.ssh/config << 'EOF'

Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_deploy
  StrictHostKeyChecking no
EOF
    chmod 600 ~/.ssh/config
    echo "✅ SSH config updated"
else
    echo "ℹ️  SSH config already has github_deploy entry"
fi

# -----------------------------------------------
# Step 3: Clone repository
# -----------------------------------------------
echo ""
echo "📋 Step 3: Clone repository"

if [ ! -d "$PROJECT_DIR/.git" ]; then
    git clone git@github.com:KirillAISREDA/Taxnavigator_agent.git "$PROJECT_DIR"
    echo "✅ Repository cloned to $PROJECT_DIR"
else
    echo "ℹ️  Repository already cloned at $PROJECT_DIR"
    cd "$PROJECT_DIR" && git pull origin main
fi

# -----------------------------------------------
# Step 4: Create .env
# -----------------------------------------------
echo ""
echo "📋 Step 4: Environment configuration"

cd "$PROJECT_DIR"

if [ ! -f .env ]; then
    cp .env.example .env
    # Generate random secret key
    SECRET=$(openssl rand -hex 32)
    sed -i "s/generate-a-random-secret-key-here/$SECRET/" .env
    echo "✅ .env created with random APP_SECRET_KEY"
    echo ""
    echo "⚠️  You MUST edit .env and add your OPENAI_API_KEY:"
    echo "   nano $PROJECT_DIR/.env"
    echo ""
    read -p "Press ENTER when you've configured .env..."
else
    echo "ℹ️  .env already exists"
fi

# -----------------------------------------------
# Step 5: Make deploy script executable
# -----------------------------------------------
chmod +x "$PROJECT_DIR/deploy.sh"

# -----------------------------------------------
# Step 6: Generate webhook secret
# -----------------------------------------------
echo ""
echo "📋 Step 6: Webhook setup"

WEBHOOK_SECRET=$(openssl rand -hex 20)
echo ""
echo "═══════════════════════════════════════════"
echo "🔑 YOUR WEBHOOK SECRET (save this!):"
echo "   $WEBHOOK_SECRET"
echo "═══════════════════════════════════════════"
echo ""

# Update systemd service with the secret
sed -i "s/CHANGE_ME_TO_RANDOM_STRING/$WEBHOOK_SECRET/" "$PROJECT_DIR/webhook/taxnav-webhook.service"

# -----------------------------------------------
# Step 7: Install webhook as systemd service
# -----------------------------------------------
echo "📋 Step 7: Installing webhook service"

cp "$PROJECT_DIR/webhook/taxnav-webhook.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable taxnav-webhook
systemctl start taxnav-webhook

echo "✅ Webhook listener running on port 9000"

# -----------------------------------------------
# Step 8: Configure nginx for webhook
# -----------------------------------------------
echo ""
echo "📋 Step 8: Nginx configuration"
echo ""
echo "Add this to your nginx config (or the existing server block):"
echo ""
echo "  # GitHub webhook endpoint"
echo "  location /deploy/webhook {"
echo "      proxy_pass http://127.0.0.1:9000/webhook;"
echo "      proxy_set_header X-Real-IP \$remote_addr;"
echo "      proxy_set_header X-Hub-Signature-256 \$http_x_hub_signature_256;"
echo "      proxy_set_header X-GitHub-Event \$http_x_github_event;"
echo "  }"
echo ""
read -p "Press ENTER when nginx is configured (or skip for now)..."

# -----------------------------------------------
# Step 9: Start Docker stack
# -----------------------------------------------
echo ""
echo "📋 Step 9: Starting Docker stack"

cd "$PROJECT_DIR"
docker-compose up -d --build

echo ""
echo "⏳ Waiting 15 seconds for services to start..."
sleep 15

# Health check
echo ""
echo "📋 Step 10: Health check"
curl -sf http://localhost:8100/health && echo "" && echo "✅ All services running!" || echo "⚠️ Health check failed — check logs with: docker-compose logs"

# -----------------------------------------------
# Final summary
# -----------------------------------------------
echo ""
echo "======================================"
echo "🎉 SETUP COMPLETE!"
echo "======================================"
echo ""
echo "📍 Project dir:    $PROJECT_DIR"
echo "🌐 API:            http://localhost:8100"
echo "🏥 Health check:   http://localhost:8100/health"
echo "💬 Chat widget:    http://localhost:8100/widget/"
echo "🔗 Webhook:        port 9000 (behind nginx)"
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Configure GitHub webhook:"
echo "   → https://github.com/KirillAISREDA/Taxnavigator_agent/settings/hooks/new"
echo "   → Payload URL: https://YOUR_DOMAIN/deploy/webhook"
echo "   → Content type: application/json"
echo "   → Secret: $WEBHOOK_SECRET"
echo "   → Events: Just the push event"
echo ""
echo "2. Test the webhook by pushing a commit"
echo ""
echo "3. Monitor logs:"
echo "   tail -f $PROJECT_DIR/deploy.log"
echo "   docker-compose logs -f"
echo ""
