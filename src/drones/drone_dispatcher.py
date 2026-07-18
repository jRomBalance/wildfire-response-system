"""
WildfireNet — Drone Dispatcher
================================
Triggers autonomous drone response via Dryad Silvaguard API.
When a WARNING or EMERGENCY alert fires, this module:
  1. Finds the nearest available drone hangar to the fire location
  2. Dispatches observation drone (infrared confirmation)
  3. On confirmation, dispatches suppression drone (retardant drop)

Dryad Silvaguard demo (Nov 2025): detect + extinguish in under 12 minutes.
Partnership/API access: https://www.dryad.net/contact

For regions without Silvaguard hardware yet, this module logs
the dispatch request and queues it for when hardware is deployed.
"""

import os
import logging
import asyncio
import httpx
import math
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Drone Hangar Registry ─────────────────────────────────────────────────────
# In production: loaded from database, updated as hardware is deployed.
# These are PLANNED deployment locations for Phase 2/3.
# Format: {id, name, lat, lon, region_id, status, drone_types}

DRONE_HANGARS = [
    {
        "id": "hangar-001",
        "name": "Sault Ste. Marie North Station",
        "lat": 46.52, "lon": -84.35,
        "region_id": "ontario-michigan-border",
        "status": "PLANNED",          # PLANNED / ACTIVE / OFFLINE
        "drone_types": ["observation", "suppression"],
        "max_range_km": 15.0,
    },
    {
        "id": "hangar-002",
        "name": "Marquette Forest Station",
        "lat": 46.54, "lon": -87.40,
        "region_id": "upper-peninsula-michigan",
        "status": "PLANNED",
        "drone_types": ["observation", "suppression"],
        "max_range_km": 15.0,
    },
    {
        "id": "hangar-003",
        "name": "Duluth Border Watch",
        "lat": 47.83, "lon": -92.18,
        "region_id": "northern-minnesota-bwca",
        "status": "PLANNED",
        "drone_types": ["observation"],
        "max_range_km": 20.0,
    },
    {
        "id": "hangar-004",
        "name": "Missoula Fire Station Alpha",
        "lat": 46.87, "lon": -113.99,
        "region_id": "northern-rockies",
        "status": "PLANNED",
        "drone_types": ["observation", "suppression"],
        "max_range_km": 15.0,
    },
    {
        "id": "hangar-005",
        "name": "Silvaguard Demo Unit (Dryad Partner)",
        "lat": 37.77, "lon": -122.41,
        "region_id": "demo",
        "status": "ACTIVE",           # Only active unit — Dryad partner hardware
        "drone_types": ["observation", "suppression"],
        "max_range_km": 10.0,
    },
]


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class DroneDispatchRequest:
    """A request to dispatch drones to a fire location."""
    alert_id: str
    target_lat: float
    target_lon: float
    alert_tier: str
    nearest_hangar_id: Optional[str]
    nearest_hangar_name: Optional[str]
    distance_km: Optional[float]
    drone_types_requested: list[str]
    requested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class DroneDispatchResult:
    """Result of a drone dispatch attempt."""
    alert_id: str
    success: bool
    hangar_id: Optional[str]
    hangar_name: Optional[str]
    distance_km: Optional[float]
    drones_dispatched: list[str] = field(default_factory=list)
    eta_minutes: Optional[float] = None
    status: str = "PENDING"           # PENDING / DISPATCHED / NO_HARDWARE / FAILED
    message: str = ""
    dispatched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


# ── Drone Dispatcher ──────────────────────────────────────────────────────────

class DroneDispatcher:
    """
    Autonomous drone response coordinator.

    Finds nearest available hangar, calculates ETA,
    and triggers dispatch via Dryad Silvaguard API (when available)
    or queues for manual dispatch (when hardware not yet deployed).

    Usage:
        dispatcher = DroneDispatcher()
        result = await dispatcher.dispatch_to_location(
            lat=46.512, lon=-84.337,
            alert_tier="WARNING",
            alert_id="alert-001"
        )
    """

    def __init__(self):
        self.api_key      = os.getenv("DRYAD_API_KEY")
        self.api_base_url = os.getenv(
            "DRYAD_API_BASE_URL", "https://api.silvanet.dryad.net/v1"
        )
        self.hangars = DRONE_HANGARS

        # Drone speed assumptions (km/h)
        self.observation_drone_speed_kmh = 60.0
        self.suppression_drone_speed_kmh = 40.0  # heavier with retardant payload

    async def dispatch_to_location(
        self,
        lat: float,
        lon: float,
        alert_tier: str,
        alert_id: str,
    ) -> bool:
        """
        Main dispatch method called by AlertDispatcher.
        Returns True if dispatch was successful or queued.
        """
        result = await self._dispatch(lat, lon, alert_tier, alert_id)
        logger.info(f"Drone dispatch result: {result.status} — {result.message}")
        return result.success

    async def _dispatch(
        self,
        lat: float,
        lon: float,
        alert_tier: str,
        alert_id: str,
    ) -> DroneDispatchResult:
        """Full dispatch logic with hangar selection and API call."""

        # Find nearest available hangar
        hangar = self._find_nearest_hangar(lat, lon)

        if not hangar:
            return DroneDispatchResult(
                alert_id=alert_id,
                success=False,
                hangar_id=None,
                hangar_name=None,
                distance_km=None,
                status="NO_HARDWARE",
                message=(
                    "No drone hangars within range. "
                    "Dispatch request logged for manual response. "
                    "Phase 2 hardware deployment needed in this region."
                ),
            )

        distance_km = _haversine_km(lat, lon, hangar["lat"], hangar["lon"])

        # Check if fire is within drone range
        if distance_km > hangar["max_range_km"]:
            return DroneDispatchResult(
                alert_id=alert_id,
                success=False,
                hangar_id=hangar["id"],
                hangar_name=hangar["name"],
                distance_km=round(distance_km, 2),
                status="OUT_OF_RANGE",
                message=(
                    f"Fire at {distance_km:.1f}km exceeds hangar range "
                    f"of {hangar['max_range_km']}km. "
                    f"Nearest hangar: {hangar['name']}."
                ),
            )

        # Determine which drones to send based on tier and availability
        drones_to_send = []
        if "observation" in hangar["drone_types"]:
            drones_to_send.append("observation")
        if alert_tier in ("WARNING", "EMERGENCY") and "suppression" in hangar["drone_types"]:
            drones_to_send.append("suppression")

        # Calculate ETA
        obs_eta = (distance_km / self.observation_drone_speed_kmh) * 60  # minutes
        eta = round(obs_eta, 1)

        # Attempt API dispatch if hangar is ACTIVE and API key available
        if hangar["status"] == "ACTIVE" and self.api_key:
            api_success = await self._call_silvaguard_api(
                hangar_id=hangar["id"],
                target_lat=lat,
                target_lon=lon,
                alert_id=alert_id,
                drone_types=drones_to_send,
            )
            if api_success:
                return DroneDispatchResult(
                    alert_id=alert_id,
                    success=True,
                    hangar_id=hangar["id"],
                    hangar_name=hangar["name"],
                    distance_km=round(distance_km, 2),
                    drones_dispatched=drones_to_send,
                    eta_minutes=eta,
                    status="DISPATCHED",
                    message=(
                        f"Drones dispatched from {hangar['name']} "
                        f"({distance_km:.1f}km away). "
                        f"ETA: ~{eta:.0f} min. "
                        f"Types: {', '.join(drones_to_send)}."
                    ),
                )

        # Hangar is PLANNED (not yet deployed) — queue and log
        logger.warning(
            f"Drone hangar {hangar['name']} is PLANNED (not yet deployed). "
            f"Logging dispatch request for manual follow-up."
        )
        self._queue_manual_dispatch(
            alert_id=alert_id,
            lat=lat, lon=lon,
            hangar=hangar,
            drones=drones_to_send,
            eta=eta,
        )

        return DroneDispatchResult(
            alert_id=alert_id,
            success=True,   # True = request was handled (queued)
            hangar_id=hangar["id"],
            hangar_name=hangar["name"],
            distance_km=round(distance_km, 2),
            drones_dispatched=drones_to_send,
            eta_minutes=eta,
            status="QUEUED",
            message=(
                f"Hardware not yet deployed at {hangar['name']}. "
                f"Dispatch queued for manual response. "
                f"Nearest planned hangar: {distance_km:.1f}km away. "
                f"Estimated ETA when deployed: ~{eta:.0f} min."
            ),
        )

    def _find_nearest_hangar(
        self, lat: float, lon: float
    ) -> Optional[dict]:
        """
        Find the nearest hangar (ACTIVE or PLANNED) to the fire location.
        Prefers ACTIVE hangars over PLANNED ones.
        """
        active_hangars = [h for h in self.hangars if h["status"] == "ACTIVE"]
        planned_hangars = [h for h in self.hangars if h["status"] == "PLANNED"]

        # Try active first
        for pool in [active_hangars, planned_hangars]:
            if not pool:
                continue
            nearest = min(
                pool,
                key=lambda h: _haversine_km(lat, lon, h["lat"], h["lon"]),
            )
            return nearest

        return None

    async def _call_silvaguard_api(
        self,
        hangar_id: str,
        target_lat: float,
        target_lon: float,
        alert_id: str,
        drone_types: list[str],
    ) -> bool:
        """
        Call Dryad Silvaguard API to trigger autonomous drone dispatch.
        API docs: https://docs.dryad.app/
        Partnership required: https://www.dryad.net/contact
        """
        payload = {
            "hangar_id": hangar_id,
            "mission": {
                "type": "wildfire_response",
                "target": {"latitude": target_lat, "longitude": target_lon},
                "drone_types": drone_types,
                "alert_id": alert_id,
                "priority": "HIGH",
            }
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.api_base_url}/missions/dispatch",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                data = response.json()
                logger.info(f"Silvaguard API response: {data}")
                return True

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Silvaguard API error: {e.response.status_code} — {e.response.text}"
            )
            return False
        except Exception as e:
            logger.error(f"Silvaguard API call failed: {e}")
            return False

    def _queue_manual_dispatch(
        self,
        alert_id: str,
        lat: float,
        lon: float,
        hangar: dict,
        drones: list[str],
        eta: float,
    ):
        """
        Log a manual dispatch request when hardware isn't deployed yet.
        In production: write to database queue, trigger human notification.
        """
        logger.critical(
            f"MANUAL DRONE DISPATCH REQUIRED:\n"
            f"  Alert ID:     {alert_id}\n"
            f"  Fire GPS:     {lat:.5f}, {lon:.5f}\n"
            f"  Maps:         https://maps.google.com/?q={lat},{lon}\n"
            f"  Nearest site: {hangar['name']} ({hangar['id']})\n"
            f"  Drone types:  {', '.join(drones)}\n"
            f"  Est. ETA:     ~{eta:.0f} min when deployed\n"
            f"  Action:       Deploy hardware to {hangar['name']} ASAP\n"
            f"  Dryad info:   https://www.dryad.net/contact"
        )

    async def get_hangar_status(self) -> list[dict]:
        """Return status of all registered drone hangars."""
        return [
            {
                "id": h["id"],
                "name": h["name"],
                "lat": h["lat"],
                "lon": h["lon"],
                "region_id": h["region_id"],
                "status": h["status"],
                "drone_types": h["drone_types"],
                "max_range_km": h["max_range_km"],
            }
            for h in self.hangars
        ]


# ── Geometry Helper ───────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in kilometers."""
    R = 6371.0  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── CLI Test ──────────────────────────────────────────────────────────────────

async def main():
    """Test drone dispatcher with simulated fire at Ontario-Michigan border."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold red]🚁 WildfireNet — Drone Dispatcher Test[/bold red]\n")

    dispatcher = DroneDispatcher()

    # Show hangar registry
    hangars = await dispatcher.get_hangar_status()
    table = Table(title="Drone Hangar Registry")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Region")
    table.add_column("Status", style="bold")
    table.add_column("Drones")
    table.add_column("Range (km)")

    for h in hangars:
        status_color = "green" if h["status"] == "ACTIVE" else "yellow"
        table.add_row(
            h["id"], h["name"], h["region_id"],
            f"[{status_color}]{h['status']}[/{status_color}]",
            ", ".join(h["drone_types"]),
            str(h["max_range_km"]),
        )
    console.print(table)

    # Simulate dispatch to Ontario-Michigan border fire
    console.print("\n[yellow]Simulating WARNING dispatch to Ontario–Michigan border...[/yellow]")

    result = await dispatcher._dispatch(
        lat=46.512, lon=-84.337,
        alert_tier="WARNING",
        alert_id="test-drone-001",
    )

    console.print(f"\n[bold]Dispatch Result:[/bold]")
    console.print(f"  Status:   [bold]{'[green]' if result.success else '[red]'}{result.status}[/bold]")
    console.print(f"  Hangar:   {result.hangar_name or 'None'}")
    console.print(f"  Distance: {result.distance_km} km")
    console.print(f"  Drones:   {result.drones_dispatched}")
    console.print(f"  ETA:      {result.eta_minutes} min")
    console.print(f"  Message:  {result.message}")

    console.print("\n[green]✓ Drone dispatcher test complete.[/green]\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())