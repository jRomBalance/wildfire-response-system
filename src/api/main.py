"""
WildfireNet — FastAPI Application
===================================
Main API server. Exposes endpoints for:
  - Active fire data (from NASA FIRMS)
  - AQI / PM2.5 smoke conditions (from EPA AirNow)
  - Alert triggering (manual + automated)
  - Drone dispatch status
  - Priority region registry
  - Real-time WebSocket fire event stream

Run:
    uvicorn src.api.main:app --reload --port 8000

Docs auto-generated at:
    http://localhost:8000/docs      (Swagger UI)
    http://localhost:8000/redoc     (ReDoc)
"""

import os
import logging
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── App Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    logger.info("🔥 WildfireNet API starting up...")

    # Start background polling task
    poll_task = asyncio.create_task(_background_fire_poll())
    app.state.poll_task = poll_task

    yield

    # Shutdown
    logger.info("WildfireNet API shutting down...")
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="WildfireNet API",
    description=(
        "Autonomous wildfire detection and response system. "
        "Fuses NASA satellite data, EPA AQI, IoT sensors, and drone dispatch "
        "into a unified real-time alert platform."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow dashboard and mobile apps to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve public HTML pages directly from Railway ─────────────────────────────
# This bypasses WordPress JS stripping issues
import os as _os
_public_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "docs", "public")
_public_dir = _os.path.abspath(_public_dir)

if _os.path.exists(_public_dir):
    app.mount("/public", StaticFiles(directory=_public_dir), name="public")

@app.get("/wildfire", response_class=HTMLResponse, tags=["Pages"])
async def wildfire_page():
    """Serve the WildfireNet public page directly from Railway."""
    index_path = _os.path.join(_public_dir, "index.html")
    if _os.path.exists(index_path):
        return HTMLResponse(content=open(index_path, encoding="utf-8").read())
    return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/wildfire/privacy", response_class=HTMLResponse, tags=["Pages"])
async def privacy_page():
    path = _os.path.join(_public_dir, "privacy.html")
    if _os.path.exists(path):
        return HTMLResponse(content=open(path, encoding="utf-8").read())
    return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/wildfire/terms", response_class=HTMLResponse, tags=["Pages"])
async def terms_page():
    path = _os.path.join(_public_dir, "terms.html")
    if _os.path.exists(path):
        return HTMLResponse(content=open(path, encoding="utf-8").read())
    return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

# ── WebSocket Connection Manager ──────────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections for real-time fire event streaming."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast a fire event to all connected clients."""
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.active_connections.remove(d)


manager = ConnectionManager()


# ── Pydantic Request/Response Models ─────────────────────────────────────────

class ManualAlertRequest(BaseModel):
    region: str = Field(..., description="Region ID from priority_regions.json")
    tier: str = Field(..., description="WATCH / ADVISORY / WARNING / EMERGENCY")
    lat: float = Field(..., description="Fire latitude")
    lon: float = Field(..., description="Fire longitude")
    severity: int = Field(default=3, ge=0, le=5, description="Severity score 0–5")
    description: Optional[str] = Field(default=None, description="Optional description")
    source: str = Field(default="manual", description="Detection source")

    model_config = {"json_schema_extra": {
        "example": {
            "region": "ontario-michigan-border",
            "tier": "WARNING",
            "lat": 46.512,
            "lon": -84.337,
            "severity": 3,
            "description": "Smoke column visible from highway camera",
            "source": "camera_observation",
        }
    }}


class FireQueryRequest(BaseModel):
    north: float
    south: float
    east: float
    west: float
    days: int = Field(default=1, ge=1, le=10)
    source: str = Field(default="VIIRS_SNPP")
    min_confidence: str = Field(default="nominal")


class AlertResponse(BaseModel):
    alert_id: str
    tier: str
    status: str
    channels_succeeded: list[str]
    channels_failed: list[str]
    drone_dispatched: bool
    message: str


# ── Health & Status ───────────────────────────────────────────────────────────

@app.get("/", tags=["Status"])
async def root():
    return {
        "service": "WildfireNet API",
        "version": "0.1.0",
        "status": "operational",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "docs": "/docs",
        "websocket": "ws://localhost:8000/ws/fires/live",
    }


@app.get("/health", tags=["Status"])
async def health():
    """Health check endpoint for load balancers and monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_websockets": len(manager.active_connections),
    }


# ── Fire Detection Endpoints ──────────────────────────────────────────────────

@app.get("/api/v1/fires/active", tags=["Fire Detection"])
async def get_active_fires(days: int = 1):
    """
    Get active fire summary across all priority regions.
    Queries NASA FIRMS VIIRS satellite data.
    """
    try:
        from src.detection.firms_client import FIRMSClient
        client = FIRMSClient()
        summary = await client.get_active_fires_summary(days=days)
        await client.close()
        return summary
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Active fires query failed: {e}")
        raise HTTPException(status_code=500, detail=f"Fire data unavailable: {e}")


@app.post("/api/v1/fires/query", tags=["Fire Detection"])
async def query_fires_bbox(request: FireQueryRequest):
    """
    Query fire detections in a custom bounding box.
    Returns individual fire detection points with FRP, confidence, severity.
    """
    try:
        from src.detection.firms_client import FIRMSClient
        client = FIRMSClient()
        result = await client.query_region(
            north=request.north,
            south=request.south,
            east=request.east,
            west=request.west,
            days=request.days,
            source=request.source,
            min_confidence=request.min_confidence,
        )
        await client.close()

        return {
            "source": result.source,
            "bbox": result.bbox,
            "days": result.days,
            "queried_at": result.queried_at,
            "total_count": result.total_count,
            "high_confidence_count": result.high_confidence_count,
            "error": result.error,
            "detections": [d.to_dict() for d in result.detections],
        }
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Fire bbox query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/fires/region/{region_id}", tags=["Fire Detection"])
async def get_fires_by_region(region_id: str, days: int = 1):
    """
    Get fire detections for a specific priority region by ID.
    Region IDs: see /api/v1/regions for full list.
    """
    import json

    # Load region definition
    try:
        with open("data/regions/priority_regions.json") as f:
            regions_data = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Region data not found")

    region = next(
        (r for r in regions_data["regions"] if r["id"] == region_id), None
    )
    if not region:
        raise HTTPException(status_code=404, detail=f"Region '{region_id}' not found")

    try:
        from src.detection.firms_client import FIRMSClient
        client = FIRMSClient()
        bbox = region["bbox"]
        result = await client.query_region(
            north=bbox["north"], south=bbox["south"],
            east=bbox["east"], west=bbox["west"],
            days=days, source="VIIRS_SNPP", min_confidence="nominal",
        )
        await client.close()

        return {
            "region": {
                "id": region["id"],
                "name": region["name"],
                "priority": region["priority"],
                "status": region["status"],
                "current_coverage": region["current_coverage"],
            },
            "fire_data": {
                "queried_at": result.queried_at,
                "total_detections": result.total_count,
                "high_confidence": result.high_confidence_count,
                "detections": [d.to_dict() for d in result.detections],
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── AQI / Smoke Endpoints ─────────────────────────────────────────────────────

@app.get("/api/v1/aqi/summary", tags=["Air Quality"])
async def get_aqi_summary():
    """
    Get PM2.5 / AQI summary across all monitored cities.
    Detects regional wildfire smoke events (like the July 2026 Michigan event).
    """
    try:
        from src.detection.airnow_client import AirNowClient
        client = AirNowClient()
        summary = await client.get_smoke_event_summary()
        await client.close()

        return {
            "queried_at": summary.queried_at,
            "smoke_event_detected": summary.smoke_event_detected,
            "worst_city": summary.worst_city,
            "worst_aqi": summary.worst_aqi,
            "cities_hazardous": summary.cities_hazardous,
            "cities_very_unhealthy": summary.cities_very_unhealthy,
            "cities_unhealthy": summary.cities_unhealthy,
            "affected_regions": summary.affected_regions,
            "readings": [r.to_dict() for r in summary.readings],
        }
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



async def get_aqi_city(city: str, state: str):
    """
    Get current AQI for a specific city.
    Example: /api/v1/aqi/city?city=Detroit&state=MI
    """
    try:
        from src.detection.airnow_client import AirNowClient
        client = AirNowClient()
        readings = await client.get_current_aqi(city=city, state=state)
        await client.close()

        return {
            "city": city,
            "state": state,
            "readings": [r.to_dict() for r in readings],
        }
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Alert Endpoints ───────────────────────────────────────────────────────────

@app.post("/api/v1/alert", response_model=AlertResponse, tags=["Alerts"])
async def trigger_alert(request: ManualAlertRequest, background_tasks: BackgroundTasks):
    """
    Manually trigger a fire alert.
    Fans out to SMS, email, webhook, and drone dispatch based on tier.
    Also broadcasts to all connected WebSocket clients.
    """
    from src.alerts.alert_dispatcher import AlertDispatcher, FireAlert

    alert = FireAlert(
        alert_id=f"manual-{uuid.uuid4().hex[:8]}",
        tier=request.tier,
        source=request.source,
        latitude=request.lat,
        longitude=request.lon,
        region_id=request.region,
        region_name=request.region.replace("-", " ").title(),
        severity_score=request.severity,
        frp_mw=None,
        aqi_pm25=None,
        confidence="nominal",
        description=request.description or f"Manual alert for region {request.region}",
    )

    dispatcher = AlertDispatcher()
    result = await dispatcher.dispatch(alert)

    # Broadcast to WebSocket clients
    background_tasks.add_task(
        manager.broadcast,
        {
            "event": "fire_alert",
            "alert_id": alert.alert_id,
            "tier": alert.tier,
            "lat": alert.latitude,
            "lon": alert.longitude,
            "region_id": alert.region_id,
            "severity": alert.severity_score,
            "timestamp": alert.created_at,
        }
    )

    return AlertResponse(
        alert_id=result.alert_id,
        tier=result.tier,
        status="dispatched" if result.success else "partial_failure",
        channels_succeeded=result.channels_succeeded,
        channels_failed=result.channels_failed,
        drone_dispatched=result.drone_dispatched,
        message=result.summary(),
    )


# ── Drone Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/v1/drones/hangars", tags=["Drones"])
async def get_drone_hangars():
    """Get status of all registered drone hangars."""
    from src.drones.drone_dispatcher import DroneDispatcher
    dispatcher = DroneDispatcher()
    hangars = await dispatcher.get_hangar_status()
    return {"hangars": hangars, "total": len(hangars)}


@app.post("/api/v1/drones/dispatch", tags=["Drones"])
async def dispatch_drone(lat: float, lon: float, alert_id: Optional[str] = None):
    """
    Manually trigger drone dispatch to a GPS location.
    Finds nearest available hangar and launches observation + suppression drones.
    """
    from src.drones.drone_dispatcher import DroneDispatcher
    dispatcher = DroneDispatcher()
    aid = alert_id or f"manual-drone-{uuid.uuid4().hex[:8]}"
    result = await dispatcher._dispatch(
        lat=lat, lon=lon,
        alert_tier="WARNING",
        alert_id=aid,
    )
    return result.to_dict()


# ── Subscriber Endpoints ─────────────────────────────────────────────────────

class SubscriberRequest(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    region_id: Optional[str] = None
    zip_code: Optional[str] = None
    sms_consent: bool = False
    email_consent: bool = False
    opt_in_method: str = "web_form"

    model_config = {"json_schema_extra": {
        "example": {
            "name": "Jerry Allen",
            "phone": "+15551234567",
            "email": "jerry@example.com",
            "region_id": "ontario-michigan-border",
            "zip_code": "49783",
            "sms_consent": True,
            "email_consent": True,
        }
    }}


@app.post("/api/v1/subscribers", tags=["Subscribers"])
async def add_subscriber(request: SubscriberRequest):
    """
    Add a new subscriber to receive wildfire alerts.
    Called by the web opt-in form at romallen.com/wildfire
    """
    from src.models.fire_event_db import add_subscriber as db_add
    result = db_add(
        name=request.name,
        phone=request.phone,
        email=request.email,
        region_id=request.region_id,
        zip_code=request.zip_code,
        sms_consent=request.sms_consent,
        email_consent=request.email_consent,
        opt_in_method=request.opt_in_method,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.delete("/api/v1/subscribers/{phone}", tags=["Subscribers"])
async def opt_out_subscriber(phone: str):
    """Process STOP/opt-out request for a phone number."""
    from src.models.fire_event_db import opt_out_subscriber as db_opt_out
    success = db_opt_out(phone)
    if not success:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return {"success": True, "message": "Unsubscribed successfully"}


@app.post("/api/v1/subscribers/unsubscribe", tags=["Subscribers"])
async def unsubscribe(email: Optional[str] = None, phone: Optional[str] = None):
    """
    Unsubscribe by email or phone.
    Used by the website opt-out form — no login required.
    """
    from src.models.fire_event_db import opt_out_subscriber as db_opt_out_phone
    from src.models.fire_event_db import opt_out_by_email
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Email or phone required")
    success = False
    if phone:
        success = db_opt_out_phone(phone)
    if email and not success:
        success = opt_out_by_email(email)
    if not success:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return {"success": True, "message": "You have been unsubscribed from WildfireNet alerts."}


@app.get("/api/v1/subscribers/lookup", tags=["Subscribers"])
async def lookup_subscriber(email: Optional[str] = None, phone: Optional[str] = None):
    """
    Check if someone is already subscribed.
    Used by website form to show 'already registered' message.
    """
    from src.models.fire_event_db import get_subscriber_by_contact
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Email or phone required")
    sub = get_subscriber_by_contact(phone=phone, email=email)
    if not sub:
        return {"found": False}
    return {
        "found": True,
        "name": sub["name"],
        "regions": sub.get("regions", "").split(",") if sub.get("regions") else [],
        "sms_consent": bool(sub["sms_consent"]),
        "email_consent": bool(sub["email_consent"]),
    }


@app.get("/api/v1/subscribers", tags=["Subscribers"])
async def get_subscribers():
    """Get all active subscribers (admin use)."""
    from src.models.fire_event_db import get_all_active_subscribers
    subs = get_all_active_subscribers()
    return {"total": len(subs), "subscribers": subs}


@app.get("/api/v1/analytics/fires", tags=["Analytics"])
async def get_fire_analytics(days: int = 7):
    """Get fire detection analytics for the last N days."""
    from src.models.fire_event_db import get_fire_summary
    return get_fire_summary(days=days)



# -- Alert Management ----------------------------------------------------------

class ManageUpdateRequest(BaseModel):
    token: str
    regions: list


@app.get("/manage", response_class=HTMLResponse, tags=["Management"])
async def manage_request_page():
    """Landing page - enter email to get management link."""
    return HTMLResponse(content=open(
        os.path.join(os.path.dirname(__file__), "manage_landing.html"), encoding="utf-8"
    ).read() if os.path.exists(
        os.path.join(os.path.dirname(__file__), "manage_landing.html")
    ) else _manage_landing_html())


def _manage_landing_html():
    lines = [
        "<!DOCTYPE html><html><head><meta charset='UTF-8'/>",
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'/>",
        "<title>Manage Alerts - WildfireNet</title>",
        "<style>*{margin:0;padding:0;box-sizing:border-box}",
        "body{font-family:Arial,sans-serif;background:#1a1a1a;color:white;min-height:100vh;",
        "display:flex;align-items:center;justify-content:center;padding:20px}",
        ".card{background:#2a2a2a;border-radius:16px;padding:48px 40px;max-width:480px;",
        "width:100%;border:1px solid #333;text-align:center}",
        "h1{font-size:28px;margin-bottom:8px;color:#FF5722}",
        "p{color:#aaa;font-size:15px;margin-bottom:28px;line-height:1.6}",
        "input{width:100%;padding:14px 18px;border-radius:8px;border:1px solid #444;",
        "background:#333;color:white;font-size:16px;margin-bottom:16px}",
        "button{width:100%;padding:16px;background:#FF5722;color:white;border:none;",
        "border-radius:8px;font-size:16px;font-weight:700;cursor:pointer}",
        ".ok{background:#1b5e20;border:1px solid #4CAF50;border-radius:8px;",
        "padding:16px;margin-top:16px;display:none;font-size:14px}",
        ".back{margin-top:24px;font-size:14px}",
        ".back a{color:#FF5722;text-decoration:none}",
        "</style></head><body>",
        "<div class='card'>",
        "<h1>WildfireNet</h1>",
        "<p>Enter your email address and we will send you a secure link to manage your alert preferences.</p>",
        "<input type='email' id='em' placeholder='your@email.com'/>",
        "<button onclick='go()'>Send Management Link</button>",
        "<div class='ok' id='ok'>Check your inbox! Link expires in 24 hours.</div>",
        "<div class='back'><a href='/wildfire'>Back to WildfireNet</a></div>",
        "</div>",
        "<script>",
        "var A='https://wild-fire-response-production.up.railway.app';",
        "function go(){",
        "var e=document.getElementById('em').value.trim();",
        "if(!e){alert('Please enter your email.');return;}",
        "fetch(A+'/api/v1/manage/request?email='+encodeURIComponent(e),{method:'POST'})",
        ".then(function(r){return r.json();})",
        ".then(function(){document.getElementById('ok').style.display='block';})",
        ".catch(function(err){alert('Error: '+err.message);});}",
        "document.getElementById('em').addEventListener('keypress',function(e){",
        "if(e.key==='Enter')go();});",
        "</script></body></html>",
    ]
    return "".join(lines)



@app.get("/api/v1/manage/validate", tags=["Management"])
async def validate_manage_token_endpoint(token: str):
    """Validate a magic link token and return subscriber info."""
    from src.models.fire_event_db import validate_manage_token, get_subscriber_regions, get_subscriber_by_contact
    email = validate_manage_token(token)
    if not email:
        return {"valid": False}
    sub = get_subscriber_by_contact(email=email)
    regions = get_subscriber_regions(email)
    return {
        "valid": True,
        "email": email,
        "name": sub.get("name", "there") if sub else "there",
        "regions": regions,
    }


@app.post("/api/v1/manage/request", tags=["Management"])
async def request_manage_link(email: str, background_tasks: BackgroundTasks):
    """Send a magic link email to manage alert preferences."""
    from src.models.fire_event_db import create_manage_token, get_subscriber_by_contact
    sub = get_subscriber_by_contact(email=email)
    if not sub:
        return {"success": True, "message": "If that email is registered, you will receive a management link shortly."}
    token = create_manage_token(email)
    if not token:
        raise HTTPException(status_code=500, detail="Could not generate management link")
    manage_url = "https://wild-fire-response-production.up.railway.app/manage/" + token
    subject = "WildfireNet - Manage Your Alert Preferences"
    body = (
        "<html><body style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;'>"
        "<div style='background:#2d1515;padding:40px;border-radius:12px 12px 0 0;text-align:center;'>"
        "<h1 style='color:white;margin:0;'>WildfireNet</h1>"
        "<p style='color:rgba(255,255,255,0.8);margin:8px 0 0;'>Alert Management</p>"
        "</div>"
        "<div style='background:white;padding:40px;border:1px solid #e0e0e0;border-radius:0 0 12px 12px;'>"
        "<p style='font-size:16px;color:#333;'>Click the button below to manage your WildfireNet alert preferences.</p>"
        "<p style='font-size:14px;color:#666;'>This link expires in 24 hours.</p>"
        "<div style='text-align:center;margin:30px 0;'>"
        "<a href='" + manage_url + "' style='background:#FF5722;color:white;padding:16px 36px;"
        "text-decoration:none;border-radius:8px;font-weight:700;font-size:16px;'>Manage My Alerts</a>"
        "</div>"
        "<p style='font-size:13px;color:#888;'>If you did not request this, ignore this email.</p>"
        "<p style='font-size:13px;color:#888;'>ROM Technology - WildfireNet - alerts@romallen.com</p>"
        "</div></body></html>"
    )
    background_tasks.add_task(_send_manage_email, email, subject, body)
    return {"success": True, "message": "If that email is registered, you will receive a management link shortly."}


async def _send_manage_email(email: str, subject: str, body: str):
    try:
        from src.alerts.email_notifier import EmailNotifier
        notifier = EmailNotifier()
        await notifier.send(to=email, subject=subject, body=body)
        logger.info("Management link sent to " + email)
    except Exception as e:
        logger.error("Management email failed: " + str(e))


@app.post("/api/v1/manage/update", tags=["Management"])
async def update_manage_preferences(request: ManageUpdateRequest):
    """Update subscriber regions via management dashboard."""
    from src.models.fire_event_db import validate_manage_token, update_subscriber_regions
    email = validate_manage_token(request.token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired link. Please request a new one.")
    if not request.regions:
        raise HTTPException(status_code=400, detail="At least one region required")
    success = update_subscriber_regions(email, request.regions)
    if not success:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    return {"success": True, "message": "Updated " + str(len(request.regions)) + " watch zones for " + email}


# ── Region Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/v1/regions", tags=["Regions"])
async def get_regions(priority: Optional[int] = None):
    """
    Get all priority coverage regions.
    Filter by priority level: ?priority=1 for most critical.
    """
    import json
    try:
        with open("data/regions/priority_regions.json") as f:
            data = json.load(f)

        regions = data["regions"]
        if priority is not None:
            regions = [r for r in regions if r["priority"] == priority]

        return {
            "total": len(regions),
            "regions": regions,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Region data not found")


@app.get("/api/v1/regions/{region_id}", tags=["Regions"])
async def get_region(region_id: str):
    """Get details for a specific priority region."""
    import json
    try:
        with open("data/regions/priority_regions.json") as f:
            data = json.load(f)

        region = next(
            (r for r in data["regions"] if r["id"] == region_id), None
        )
        if not region:
            raise HTTPException(status_code=404, detail=f"Region '{region_id}' not found")
        return region
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Region data not found")


# ── WebSocket — Real-Time Fire Event Stream ───────────────────────────────────

@app.websocket("/ws/fires/live")
async def websocket_fire_stream(websocket: WebSocket):
    """
    Real-time WebSocket stream of fire events.
    Connect from dashboard or mobile app to receive live alerts.

    Message format:
    {
        "event": "fire_alert" | "aqi_update" | "heartbeat",
        "alert_id": "...",
        "tier": "WARNING",
        "lat": 46.512,
        "lon": -84.337,
        "region_id": "ontario-michigan-border",
        "severity": 3,
        "timestamp": "2026-07-17T..."
    }
    """
    await manager.connect(websocket)
    try:
        # Send welcome message
        await websocket.send_json({
            "event": "connected",
            "message": "WildfireNet live fire stream connected",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Keep connection alive, send heartbeat every 30s
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({
                "event": "heartbeat",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "active_connections": len(manager.active_connections),
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# ── Background Fire Polling ───────────────────────────────────────────────────

async def _background_fire_poll():
    """
    Background task: polls NASA FIRMS every 10 minutes.
    Checks each priority region, finds worst detection,
    and sends alerts to subscribers if severity >= 2.
    One alert per region per 2 hours maximum.
    """
    POLL_INTERVAL_SECONDS = 600  # 10 minutes
    FIRST_POLL_DELAY = 30        # 30 seconds on startup

    logger.info("Background fire polling started (first poll in 30s, then every 10 min)")

    # Initialize database on startup
    try:
        from src.models.fire_event_db import init_db
        init_db()
        logger.info("SQLite database initialized")
    except Exception as e:
        logger.error(f"Database init failed: {e}")

    await asyncio.sleep(FIRST_POLL_DELAY)

    while True:
        try:
            nasa_key = os.getenv("NASA_FIRMS_API_KEY")
            if not nasa_key:
                logger.debug("FIRMS key not set - skipping poll")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            logger.info("Background fire poll starting...")

            from src.detection.firms_client import FIRMSClient, PRIORITY_REGIONS
            from src.alerts.alert_dispatcher import build_alert_from_firms
            from src.alerts.email_notifier import EmailNotifier
            from src.alerts.sms_notifier import SMSNotifier
            from src.alerts.alert_dispatcher import _build_sms_message, _build_email_message
            from src.models.fire_event_db import (
                was_recently_alerted, save_fire_event,
                save_alert_sent, get_subscribers_for_region
            )

            # Get active fire summary
            client = FIRMSClient()
            summary = await client.get_active_fires_summary(days=1)
            await client.close()

            new_alerts = 0
            alert = None  # will be set per region

            # Check each region that has fires
            for region_id, region_data in summary.get("by_region", {}).items():
                severity = region_data.get("max_severity", 0)
                tier = region_data.get("alert_tier", "NONE")

                # Only alert on ADVISORY (2) or higher
                if severity < 2:
                    logger.debug(f"Skipping {region_id} - severity {severity} below threshold")
                    continue

                # Check duplicate suppression (2 hour window)
                if was_recently_alerted(region_id, severity, hours=2):
                    logger.info(f"Suppressed duplicate alert for {region_id}")
                    continue

                # Get subscribers for this region
                subs = get_subscribers_for_region(region_id)
                emails = [s["email"] for s in subs if s.get("email") and s.get("email_consent")]
                phones = [s["phone"] for s in subs if s.get("phone") and s.get("sms_consent")]

                logger.info(
                    f"ALERT {tier} - {region_id}: "
                    f"{len(subs)} subscribers, {len(emails)} emails, {len(phones)} phones"
                )

                if not subs:
                    logger.info(f"No subscribers for {region_id} - skipping")
                    continue

                # Build a synthetic alert for this region
                import uuid
                from src.alerts.alert_dispatcher import FireAlert
                alert = FireAlert(
                    alert_id=f"auto-{uuid.uuid4().hex[:8]}",
                    tier=tier,
                    source="background_poll",
                    latitude={"ontario-michigan-border":(46.5,-84.5),"northern-ontario-boreal":(50.0,-85.0),"upper-peninsula-michigan":(46.4,-86.5),"alberta-bc-interior":(54.0,-115.0),"saskatchewan-boreal":(55.0,-105.0),"northern-minnesota-bwca":(47.9,-91.8),"pacific-northwest":(47.5,-120.5),"northern-rockies":(47.0,-114.0),"new-mexico-arizona-highlands":(34.0,-108.0),"quebec-boreal":(52.0,-72.0),"great-lakes-national-forests":(45.0,-88.0)}.get(region_id,(45.0,-90.0))[0],
                    longitude={"ontario-michigan-border":(46.5,-84.5),"northern-ontario-boreal":(50.0,-85.0),"upper-peninsula-michigan":(46.4,-86.5),"alberta-bc-interior":(54.0,-115.0),"saskatchewan-boreal":(55.0,-105.0),"northern-minnesota-bwca":(47.9,-91.8),"pacific-northwest":(47.5,-120.5),"northern-rockies":(47.0,-114.0),"new-mexico-arizona-highlands":(34.0,-108.0),"quebec-boreal":(52.0,-72.0),"great-lakes-national-forests":(45.0,-88.0)}.get(region_id,(45.0,-90.0))[1],
                    region_id=region_id,
                    region_name=region_id.replace("-", " ").title(),
                    severity_score=severity,
                    frp_mw=region_data.get("max_frp_mw"),
                    aqi_pm25=None,
                    confidence="nominal",
                    description=(
                        f"WildfireNet {tier} - {region_id.replace('-',' ').title()}. "
                        f"NASA FIRMS detected {region_data.get('detection_count',0)} fire hotspots. "
                        f"Max Fire Radiative Power: {region_data.get('max_frp_mw',0):.1f} MW."
                    ),
                )

                # Send email alerts
                if emails:
                    try:
                        notifier = EmailNotifier()
                        subject, body = _build_email_message(alert)
                        for email_addr in emails:
                            await notifier.send(to=email_addr, subject=subject, body=body)
                            logger.info(f"Alert email sent to {email_addr} for {region_id}")
                    except Exception as e:
                        logger.error(f"Email failed for {region_id}: {e}")

                # Send SMS alerts
                if phones:
                    try:
                        sms_notifier = SMSNotifier()
                        message = _build_sms_message(alert)
                        for phone in phones:
                            await sms_notifier.send(to=phone, message=message)
                            logger.info(f"Alert SMS sent to {phone} for {region_id}")
                    except Exception as e:
                        logger.error(f"SMS failed for {region_id}: {e}")

                # Save alert record to prevent duplicates
                save_alert_sent(alert, None)
                new_alerts += 1

                # Broadcast to WebSocket clients
                await manager.broadcast({
                    "event": "fire_alert",
                    "alert_id": alert.alert_id,
                    "tier": tier,
                    "lat": alert.latitude,
                    "lon": alert.longitude,
                    "region_id": region_id,
                    "severity": severity,
                    "frp_mw": region_data.get("max_frp_mw"),
                    "source": "background_poll",
                    "timestamp": alert.created_at,
                })

            logger.info(f"Background poll complete - {new_alerts} alerts dispatched")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Background poll cancelled")
            break
        except Exception as e:
            logger.error(f"Background poll error: {e}")
            await asyncio.sleep(60)




if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
        log_level="info",
    )