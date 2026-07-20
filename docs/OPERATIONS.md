# 🔥 WildfireNet — Operations Guide
> Quick reference for running, testing, and managing the system.
> Last updated: July 20, 2026

---

## 🌐 Live System URLs

| Resource | URL |
|----------|-----|
| **Public Website** | https://romallen.com/wildfire/index.html |
| **Live API** | https://wild-fire-response-production.up.railway.app |
| **API Docs (Swagger)** | https://wild-fire-response-production.up.railway.app/docs |
| **Health Check** | https://wild-fire-response-production.up.railway.app/health |
| **Active Fires** | https://wild-fire-response-production.up.railway.app/api/v1/fires/active |
| **AQI Summary** | https://wild-fire-response-production.up.railway.app/api/v1/aqi/summary |
| **Subscribers** | https://wild-fire-response-production.up.railway.app/api/v1/subscribers |
| **GitHub Repo** | https://github.com/jRomBalance/wildfire-response-system |
| **Railway Dashboard** | https://railway.app/dashboard |
| **NASA FIRMS Live Map** | https://firms.modaps.eosdis.nasa.gov/map/ |
| **EPA AirNow Map** | https://www.airnow.gov/ |

---

## 🚀 Local Development (Optional)

Only needed if you want to run WildfireNet on your own machine for development.
**The live system runs 24/7 on Railway — your laptop does not need to be on.**

### Setup
```powershell
cd C:\Users\Jerry\source\wildfire-response-system
.\venv\Scripts\Activate.ps1
uvicorn src.api.main:app --reload --port 8000
```

> ⚠️ If you get a permissions error:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

Local API docs: **http://localhost:8000/docs**

---

## 🧪 Testing Each Component (Local)

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
| POST | `/api/v1/subscribers` | Add subscriber |
| GET | `/api/v1/subscribers` | List all subscribers |
| GET | `/api/v1/subscribers/lookup` | Check if already subscribed |
| POST | `/api/v1/subscribers/unsubscribe` | Opt-out by email or phone |
| DELETE | `/api/v1/subscribers/{phone}` | Remove subscriber by phone |
| GET | `/api/v1/analytics/fires` | Fire detection analytics |
| WS  | `/ws/fires/live` | Real-time WebSocket fire stream |

---

## 🔥 Trigger a Manual Alert (Live Swagger)

1. Open **https://wild-fire-response-production.up.railway.app/docs**
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
| Tier | Threshold | Actions Triggered |
|------|-----------|------------------|
| `WATCH` | Severity 1 | Log only |
| `ADVISORY` | Severity 2+ | Email alert to subscribers |
| `WARNING` | Severity 3+ | SMS + Email + Drone dispatch |
| `EMERGENCY` | Severity 4-5 | All channels + Cross-border |

---

## 🔑 Environment Variables

### Railway (Production)
Set these in Railway Dashboard → Variables tab:
```
NASA_FIRMS_API_KEY=
NASA_FIRMS_MAP_KEY=
EPA_AIRNOW_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=+14133279996
FIREFIGHTER_PHONES=+14134296942
SENDGRID_API_KEY=
ALERT_FROM_EMAIL=jerome.martin.allen@gmail.com
FIREFIGHTER_EMAILS=jerome.martin.allen@gmail.com
DB_PATH=wildfirenet.db
APP_ENV=production
LOG_LEVEL=INFO
PORT=8000
```

### Local Development (.env file)
Same variables as above — copy `.env.example` to `.env` and fill in values.
**Never commit `.env` to GitHub.**

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
□ Check https://wild-fire-response-production.up.railway.app/health
□ Check /api/v1/fires/active — any new WARNING/EMERGENCY regions?
□ Check /api/v1/aqi/summary — smoke event detected?
□ Check /api/v1/subscribers — new signups from romallen.com/wildfire?
□ Check email inbox for any auto-triggered alerts
□ Check Railway dashboard for deployment status
```

---

## 🐛 Common Issues & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError` | venv not activated | Run `.\venv\Scripts\Activate.ps1` |
| `NASA key required` | Missing .env key | Add `NASA_FIRMS_API_KEY` to .env |
| `401 Unauthorized` (SendGrid) | Wrong/incomplete API key | Regenerate key at SendGrid |
| `KYC not approved` (Twilio) | A2P registration pending | Wait for Trust Hub approval |
| `Webhook failed` | No webhook URL set | Comment out `ALERT_WEBHOOK_URL` in .env |
| Railway not deploying | Push not detected | Check Railway dashboard → Deployments |
| Live data not loading on website | CORS or API down | Check Railway health endpoint |

---

## 🗺️ Roadmap

### ✅ Phase 1 — Complete
- NASA FIRMS satellite integration (live)
- EPA AirNow PM2.5 integration (live)
- Email alerts via SendGrid (live)
- SMS alerts via Twilio (campaign pending approval)
- FastAPI backend + WebSocket stream (live on Railway)
- 11 priority regions mapped
- SQLite database with duplicate suppression
- Public website at romallen.com/wildfire
- Multi-region subscriber opt-in

### 🔄 Phase 2 — In Progress
- ✅ Live fire data on public website
- ✅ Multi-region watch zones
- ✅ Opt-out form on website
- ✅ Railway cloud deployment (24/7)
- ⬜ AI fusion engine (cross-validate satellite + IoT + camera)
- ⬜ Mapbox live fire dashboard
- ⬜ Cross-border CIFFC Canada alert integration
- ⬜ Agency outreach (Michigan EGLE, MN DNR)

### 🔵 Phase 3 — Scale
- Dryad Silvaguard drone integration
- IoT sensor pilot deployment (Upper Michigan / Ontario border)
- USFS + CIFFC agency partnerships
- CAD system integration (fire dispatch centers)
- Custom domain: api.romallen.com