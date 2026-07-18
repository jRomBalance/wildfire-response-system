#!/bin/bash
# ============================================================
# WildfireNet — One-Command Dev Environment Setup
# Usage: bash scripts/setup_dev.sh
# ============================================================

set -e  # Exit on any error

echo ""
echo "🔥 WildfireNet — Dev Environment Setup"
echo "======================================="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python: $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "→ Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Activate venv
source venv/bin/activate
echo "✓ Virtual environment activated"

# Upgrade pip
pip install --upgrade pip --quiet
echo "✓ pip upgraded"

# Install dependencies
echo "→ Installing dependencies..."
pip install -r requirements.txt --quiet
echo "✓ Dependencies installed"

# Create .env from template if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "✓ .env created from template"
    echo ""
    echo "⚠️  ACTION REQUIRED: Edit .env and add your API keys:"
    echo ""
    echo "   🔴 PRIORITY 1 — Get these first (all free):"
    echo "   NASA FIRMS:  https://firms.modaps.eosdis.nasa.gov/api/map_key/"
    echo "   EPA AirNow:  https://docs.airnowapi.org/account/request/"
    echo "   GitHub:      https://github.com (for pushing your repo)"
    echo ""
    echo "   🟠 PRIORITY 2:"
    echo "   Twilio SMS:  https://www.twilio.com/try-twilio"
    echo "   SendGrid:    https://signup.sendgrid.com/"
    echo "   Mapbox:      https://account.mapbox.com/auth/signup/"
    echo ""
else
    echo "✓ .env already exists"
fi

# Create __init__.py files if missing
touch src/__init__.py
touch src/models/__init__.py
echo "✓ Package init files verified"

# Run a quick import test
echo "→ Testing imports..."
python3 -c "
import fastapi, httpx, pydantic, dotenv, structlog
print('✓ Core imports OK')
" 2>&1

echo ""
echo "======================================="
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys"
echo "  2. Test FIRMS client:    python src/detection/firms_client.py"
echo "  3. Test AirNow client:   python src/detection/airnow_client.py"
echo "  4. Test alert dispatch:  python src/alerts/alert_dispatcher.py"
echo "  5. Start API server:     uvicorn src.api.main:app --reload --port 8000"
echo "  6. View API docs:        http://localhost:8000/docs"
echo ""
echo "🔥 Let's catch fires before they catch us."
echo ""