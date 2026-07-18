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
from fastapi.responses import JSONResponse
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


@app.get("/api/v1/aqi/city", tags=["Air Quality"])
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
        confidence="high",
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
    On new high-severity detections, auto-triggers alert pipeline
    and broadcasts to WebSocket clients.
    """
    POLL_INTERVAL_SECONDS = 600  # 10 minutes

    logger.info("Background fire polling started (every 10 min)")

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

            nasa_key = os.getenv("NASA_FIRMS_API_KEY")
            if not nasa_key:
                logger.debug("FIRMS key not set — skipping background poll")
                continue

            from src.detection.firms_client import FIRMSClient
            from src.alerts.alert_dispatcher import AlertDispatcher, build_alert_from_firms

            client = FIRMSClient()
            results = await client.query_all_priority_regions(
                days=1, source="VIIRS_SNPP", min_confidence="high"
            )
            await client.close()

            # Auto-alert on WARNING or EMERGENCY severity
            dispatcher = AlertDispatcher()
            for result in results:
                for detection in result.detections:
                    if detection.severity_score >= 3:  # WARNING threshold
                        alert = build_alert_from_firms(detection)
                        dispatch_result = await dispatcher.dispatch(alert)

                        # Broadcast to WebSocket clients
                        await manager.broadcast({
                            "event": "fire_alert",
                            "alert_id": alert.alert_id,
                            "tier": alert.tier,
                            "lat": alert.latitude,
                            "lon": alert.longitude,
                            "region_id": alert.region_id,
                            "severity": alert.severity_score,
                            "frp_mw": alert.frp_mw,
                            "source": "background_poll",
                            "timestamp": alert.created_at,
                        })

            logger.info("Background fire poll complete")

        except asyncio.CancelledError:
            logger.info("Background poll cancelled")
            break
        except Exception as e:
            logger.error(f"Background poll error: {e}")
            await asyncio.sleep(60)  # Back off on error


# ── Entry Point ───────────────────────────────────────────────────────────────

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