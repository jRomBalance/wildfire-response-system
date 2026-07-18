# 🔥 WildfireNet — Operations Guide
> Quick reference for running, testing, and managing the system.
> Last updated: July 18, 2026

---

## 🚀 Starting the System

### Step 1 — Navigate to project
```powershell
cd C:\Users\Jerry\source\wildfire-response-system
```

### Step 2 — Activate virtual environment
```powershell
.\venv\Scripts\Activate.ps1
```
You should see `(venv)` appear at the start of your prompt.

> ⚠️ If you get a permissions error:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Step 3 — Start the API server
```powershell
uvicorn src.api.main:app --reload --port 8000
```

### Step 4 — Verify it's running
Open browser: **http://localhost:8000/docs**
You should see the WildfireNet Swagger UI.

---

## 🛑 Stopping the System
```powershell
Ctrl + C
```

---

## 🧪 Testing Each Component

### Test NASA Satellite Fire Detection
```powershell
python src/detection/firms_client.py
```
**Expected output:** Live fire detections across all 11 priority regions.
**Requires:** `NASA_FIRMS_API_KEY` in `.env`

### Test EPA AQI / PM2.5 Smoke Detection
```powershell
python src/detection/airnow_client.py
```
**Expected output:** PM2.5 AQI readings across monitored cities.
**Requires:** `EPA_AIRNOW_API_KEY` in `.env`

### Test Alert Dispatcher
```powershell
python src/alerts/alert_dispatcher.py
```
**Expected output:** Simulated WARNING alert dispatched to email/SMS.
**Requires:** SendGrid + Twilio keys in `.env`

### Test Drone Dispatcher
```powershell
python src/drones/drone_dispatcher.py
```
**Expected output:** Nearest hangar found, dispatch queued/logged.
**Requires:** Nothing — runs in simulation mode without Dryad key.

---

## 🌐 API Endpoints Quick Reference

| Method | Endpoint | What It Does |
|--------|----------|-------------|
| GET | `/` | System status |
| GET | `/health` | Health check |
| GET | `/api/v1/fires/active` | Live fires across all priority regions |
| POST | `/api/v1/fires/query` | Query fires in custom bounding box |
| GET | `/api/v1/fires/region/{id}` | Fires in specific region |
| GET | `/api/v1/aqi/summary` | PM2.5 smoke event summary |
| GET | `/api/v1/aqi/city?city=Detroit&state=MI` | AQI for specific city |
| POST | `/api/v1/alert` | Trigger manual fire alert |
| GET | `/api/v1/drones/hangars` | Drone hangar status |
| POST | `/api/v1/drones/dispatch` | Manual drone dispatch |
| GET | `/api/v1/regions` | All priority regions |
| GET | `/api/v1/regions/{id}` | Specific region details |
| WS  | `/ws/fires/live` | Real-time WebSocket fire stream |

---

## 🔥 Trigger a Manual Alert (Swagger)

1. Open **http://localhost:8000/docs**
2. Click **Alerts** → `POST /api/v1/alert` → **Try it out**
3. Paste this body:
```json
{
  "region": "ontario-michigan-border",
  "tier": "WARNING",
  "lat": 46.512,
  "lon": -84.337,
  "severity": 3,
  "description": "Fire detected — Ontario-Michigan border zone",
  "source": "manual_test"
}
```
4. Click **Execute**
5. Check email inbox for HTML alert

### Alert Tiers
| Tier | When To Use | Actions Triggered |
|------|------------|------------------|
| `WATCH` | Single anomaly, unconfirmed | Log only |
| `ADVISORY` | 2+ sensors or camera confirmation | Email alert |
| `WARNING` | Fire confirmed, small | SMS + Email + Drone |
| `EMERGENCY` | Large fire or rapid spread | All channels + Cross-border |

---

## 🔑 Environment Variables (.env)

```env
# NASA FIRMS — Satellite fire detection
NASA_FIRMS_API_KEY=your_key_here
NASA_FIRMS_MAP_KEY=your_key_here
# Get key: https://firms.modaps.eosdis.nasa.gov/api/map_key/

# EPA AirNow — PM2.5 / AQI data
EPA_AIRNOW_API_KEY=your_key_here
# Get key: https://docs.airnowapi.org/account/request/

# Twilio — SMS alerts to firefighters
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
FIREFIGHTER_PHONES=+1xxxxxxxxxx,+1xxxxxxxxxx
# Get account: https://www.twilio.com/console
# KYC required for paid accounts: https://www.twilio.com/console/trust-hub

# SendGrid — Email alerts
SENDGRID_API_KEY=SG.xxxxxxxxxxxxxxxx
ALERT_FROM_EMAIL=your@gmail.com
FIREFIGHTER_EMAILS=your@gmail.com,another@gmail.com
# Get key: https://app.sendgrid.com/settings/api_keys
# Verify sender: https://app.sendgrid.com/settings/sender_auth

# Mapbox — Map tiles (Phase 2)
MAPBOX_ACCESS_TOKEN=your_token_here
# Get token: https://account.mapbox.com/auth/signup/

# Dryad Silvaguard — Autonomous drone response (Phase 3)
DRYAD_API_KEY=your_key_here
# Partnership required: https://www.dryad.net/contact

# App settings
APP_ENV=development
LOG_LEVEL=INFO
PORT=8000
```

---

## 📍 Priority Regions Reference

| ID | Name | Priority | Status |
|----|------|----------|--------|
| `ontario-michigan-border` | Ontario–Michigan Border | 1 | 🔴 ACTIVE EMERGENCY |
| `northern-ontario-boreal` | Northern Ontario Boreal | 1 | 🔴 ACTIVE EMERGENCY |
| `upper-peninsula-michigan` | Upper Peninsula, Michigan | 1 | 🔴 HIGH RISK |
| `alberta-bc-interior` | Alberta / BC Interior | 1 | 🔴 HIGH RISK |
| `saskatchewan-boreal` | Saskatchewan Boreal | 1 | 🔴 HIGH RISK |
| `northern-minnesota-bwca` | Northern Minnesota / BWCA | 2 | 🟠 ACTIVE RISK |
| `pacific-northwest` | Pacific Northwest WA/OR | 2 | 🟠 SEASONAL HIGH |
| `northern-rockies` | Northern Rockies MT/ID | 2 | 🟠 SEASONAL HIGH |
| `new-mexico-arizona-highlands` | NM/AZ Highlands | 2 | 🟠 EXTREME DROUGHT |
| `quebec-boreal` | Quebec Boreal Forest | 2 | 🟠 HIGH RISK |
| `great-lakes-national-forests` | Great Lakes Forests | 3 | 🟡 MONITORING |

---

## 🔄 Daily Operations Checklist

```
□ Start API server (uvicorn command above)
□ Check http://localhost:8000/api/v1/fires/active — any new WARNING/EMERGENCY?
□ Check http://localhost:8000/api/v1/aqi/summary — smoke event detected?
□ Review PowerShell logs for background poll alerts (runs every 10 min)
□ Check email inbox for any auto-triggered alerts
```

---

## 🐛 Common Issues & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError` | venv not activated | Run `.\venv\Scripts\Activate.ps1` |
| `NASA key required` | Missing .env key | Add `NASA_FIRMS_API_KEY` to .env |
| `401 Unauthorized` (SendGrid) | Wrong/incomplete API key | Regenerate key at SendGrid, copy full `SG.xxx` key |
| `Policy evaluation failed` (Twilio) | Trial account restriction | Upgrade to paid account |
| `KYC not approved` (Twilio) | A2P registration pending | Wait for Trust Hub approval (1-4 hrs) |
| `Webhook failed` | No webhook URL set | Comment out `ALERT_WEBHOOK_URL` in .env |
| `venv not recognized` | Execution policy | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| Port 8000 already in use | Previous server still running | `Ctrl+C` or restart PowerShell |

---

## 📞 Key Resource Links

| Resource | URL |
|----------|-----|
| NASA FIRMS Live Map | https://firms.modaps.eosdis.nasa.gov/map/ |
| EPA AirNow Map | https://www.airnow.gov/ |
| Twilio Console | https://www.twilio.com/console |
| Twilio KYC/Trust Hub | https://www.twilio.com/console/trust-hub |
| SendGrid Dashboard | https://app.sendgrid.com |
| Dryad Networks | https://www.dryad.net |
| GitHub Repo | https://github.com/jRomBalance/wildfire-response-system |
| WildfireNet API Docs | http://localhost:8000/docs |

---

## 🗺️ Roadmap

### ✅ Phase 1 — Complete
- NASA FIRMS satellite integration
- EPA AirNow PM2.5 integration
- Email alerts (SendGrid)
- SMS alerts (Twilio — pending KYC)
- FastAPI backend + WebSocket stream
- 11 priority regions mapped

### 🔄 Phase 2 — Next Sprint
- AI fusion engine (cross-validate satellite + IoT + camera)
- Mapbox live fire dashboard
- Cross-border CIFFC Canada alert integration
- Fire spread prediction model

### 🔵 Phase 3 — Scale
- Dryad Silvaguard drone integration
- IoT sensor pilot deployment (Upper Michigan / Ontario border)
- USFS + CIFFC agency partnerships
- CAD system integration (fire dispatch centers)
