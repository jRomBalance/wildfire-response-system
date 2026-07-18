"""
WildfireNet — EPA AirNow AQI / PM2.5 Client
=============================================
Pulls real-time air quality data including PM2.5 (the particle
choking Michigan and the Northeast right now from Canadian fires).

API Docs: https://docs.airnowapi.org/
Get your free key: https://docs.airnowapi.org/account/request/
"""

import os
import logging
import httpx
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

AIRNOW_BASE_URL = "https://www.airnowapi.org/aq"

# AQI Category breakpoints (EPA standard)
AQI_CATEGORIES = {
    (0,   50):  {"label": "Good",                    "color": "green",  "health": "Air quality is satisfactory."},
    (51,  100): {"label": "Moderate",                "color": "yellow", "health": "Acceptable; some pollutants may affect sensitive groups."},
    (101, 150): {"label": "Unhealthy for Sensitive", "color": "orange", "health": "Sensitive groups may experience health effects."},
    (151, 200): {"label": "Unhealthy",               "color": "red",    "health": "Everyone may begin to experience health effects."},
    (201, 300): {"label": "Very Unhealthy",          "color": "purple", "health": "Health alert: everyone may experience serious effects."},
    (301, 500): {"label": "Hazardous",               "color": "maroon", "health": "Health warning: emergency conditions. Everyone affected."},
}

# Monitoring cities in our priority regions
MONITORING_CITIES = [
    # Ontario-Michigan border zone
    {"city": "Sault Ste. Marie", "state": "MI", "region": "ontario-michigan-border"},
    {"city": "Marquette",        "state": "MI", "region": "upper-peninsula-michigan"},
    {"city": "Traverse City",    "state": "MI", "region": "upper-peninsula-michigan"},
    {"city": "Detroit",          "state": "MI", "region": "great-lakes-national-forests"},
    # Minnesota
    {"city": "Duluth",           "state": "MN", "region": "northern-minnesota-bwca"},
    {"city": "International Falls","state":"MN","region": "northern-minnesota-bwca"},
    # Pacific Northwest
    {"city": "Seattle",          "state": "WA", "region": "pacific-northwest"},
    {"city": "Portland",         "state": "OR", "region": "pacific-northwest"},
    # Northern Rockies
    {"city": "Missoula",         "state": "MT", "region": "northern-rockies"},
    {"city": "Boise",            "state": "ID", "region": "northern-rockies"},
    # Southwest
    {"city": "Albuquerque",      "state": "NM", "region": "new-mexico-arizona-highlands"},
    {"city": "Flagstaff",        "state": "AZ", "region": "new-mexico-arizona-highlands"},
]

# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class AQIReading:
    """A single AQI reading for a location and pollutant."""
    city: str
    state: str
    latitude: float
    longitude: float
    pollutant: str          # PM2.5, PM10, O3, NO2, etc.
    aqi: int
    category: str           # Good / Moderate / Unhealthy / etc.
    category_color: str
    health_message: str
    reporting_area: str
    date_observed: str
    hour_observed: int
    region_id: Optional[str] = None
    is_wildfire_smoke: bool = False   # True if PM2.5 AQI > 100 (likely smoke)
    alert_tier: str = "NONE"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AQISummary:
    """Summary of AQI conditions across all monitored cities."""
    queried_at: str
    readings: list[AQIReading] = field(default_factory=list)
    cities_hazardous: int = 0
    cities_very_unhealthy: int = 0
    cities_unhealthy: int = 0
    cities_unhealthy_sensitive: int = 0
    worst_city: Optional[str] = None
    worst_aqi: int = 0
    worst_pollutant: str = ""
    smoke_event_detected: bool = False
    affected_regions: list[str] = field(default_factory=list)


# ── Core Client ──────────────────────────────────────────────────────────────

class AirNowClient:
    """
    Async client for EPA AirNow API.
    Tracks PM2.5 and AQI conditions in cities downwind of wildfire zones.

    Usage:
        client = AirNowClient()
        reading = await client.get_current_aqi(city="Detroit", state="MI")
        summary = await client.get_smoke_event_summary()
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("EPA_AIRNOW_API_KEY")
        if not self.api_key:
            raise ValueError(
                "EPA AirNow API key required.\n"
                "Get yours free at: https://docs.airnowapi.org/account/request/\n"
                "Then set EPA_AIRNOW_API_KEY in your .env file."
            )
        self._client = httpx.AsyncClient(timeout=20.0)

    async def get_current_aqi(
        self,
        city: str,
        state: str,
        region_id: Optional[str] = None,
    ) -> list[AQIReading]:
        """
        Get current AQI readings for a city.
        Returns list of readings (one per pollutant: PM2.5, O3, etc.)
        """
        url = f"{AIRNOW_BASE_URL}/observation/community/current/"
        params = {
            "format": "application/json",
            "city": city,
            "statecode": state,
            "distance": 25,
            "API_KEY": self.api_key,
        }

        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            readings = []
            for item in data:
                pollutant = item.get("ParameterName", "")
                aqi_val = int(item.get("AQI", 0))
                cat_num = int(item.get("Category", {}).get("Number", 1))
                cat_name = item.get("Category", {}).get("Name", "Good")

                # Get detailed category info
                cat_info = _get_aqi_category(aqi_val)

                # Detect likely wildfire smoke: PM2.5 AQI > 100
                is_smoke = (pollutant == "PM2.5" and aqi_val > 100)

                reading = AQIReading(
                    city=city,
                    state=state,
                    latitude=float(item.get("Latitude", 0)),
                    longitude=float(item.get("Longitude", 0)),
                    pollutant=pollutant,
                    aqi=aqi_val,
                    category=cat_info["label"],
                    category_color=cat_info["color"],
                    health_message=cat_info["health"],
                    reporting_area=item.get("ReportingArea", city),
                    date_observed=item.get("DateObserved", "").strip(),
                    hour_observed=int(item.get("HourObserved", 0)),
                    region_id=region_id,
                    is_wildfire_smoke=is_smoke,
                    alert_tier=_aqi_to_alert_tier(aqi_val),
                )
                readings.append(reading)

            logger.info(f"AirNow [{city}, {state}]: {len(readings)} pollutant readings")
            return readings

        except httpx.HTTPStatusError as e:
            logger.error(f"AirNow HTTP error for {city},{state}: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"AirNow error for {city},{state}: {e}")
            return []

    async def get_aqi_by_coords(
        self,
        lat: float,
        lon: float,
        distance: int = 25,
    ) -> list[AQIReading]:
        """
        Get current AQI readings near a lat/lon coordinate.
        Useful for querying near fire detection points.
        """
        url = f"{AIRNOW_BASE_URL}/observation/latLong/current/"
        params = {
            "format": "application/json",
            "latitude": lat,
            "longitude": lon,
            "distance": distance,
            "API_KEY": self.api_key,
        }

        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            readings = []
            for item in data:
                pollutant = item.get("ParameterName", "")
                aqi_val = int(item.get("AQI", 0))
                cat_info = _get_aqi_category(aqi_val)
                is_smoke = (pollutant == "PM2.5" and aqi_val > 100)

                reading = AQIReading(
                    city=item.get("ReportingArea", f"{lat},{lon}"),
                    state=item.get("StateCode", ""),
                    latitude=lat,
                    longitude=lon,
                    pollutant=pollutant,
                    aqi=aqi_val,
                    category=cat_info["label"],
                    category_color=cat_info["color"],
                    health_message=cat_info["health"],
                    reporting_area=item.get("ReportingArea", ""),
                    date_observed=item.get("DateObserved", "").strip(),
                    hour_observed=int(item.get("HourObserved", 0)),
                    is_wildfire_smoke=is_smoke,
                    alert_tier=_aqi_to_alert_tier(aqi_val),
                )
                readings.append(reading)

            return readings

        except Exception as e:
            logger.error(f"AirNow coords query failed ({lat},{lon}): {e}")
            return []

    async def get_smoke_event_summary(self) -> AQISummary:
        """
        Query all monitored cities concurrently.
        Returns a summary of current smoke/AQI conditions.
        Designed to detect regional wildfire smoke events like the
        July 2026 Canadian fire event affecting Michigan.
        """
        summary = AQISummary(
            queried_at=datetime.now(timezone.utc).isoformat()
        )

        # Query all cities concurrently
        tasks = [
            self.get_current_aqi(
                city=c["city"],
                state=c["state"],
                region_id=c.get("region"),
            )
            for c in MONITORING_CITIES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_readings = []
        for r in results:
            if isinstance(r, list):
                all_readings.extend(r)

        # Focus on PM2.5 (wildfire smoke indicator)
        pm25_readings = [r for r in all_readings if r.pollutant == "PM2.5"]
        summary.readings = pm25_readings

        # Tally by category
        for r in pm25_readings:
            if r.aqi >= 301:
                summary.cities_hazardous += 1
            elif r.aqi >= 201:
                summary.cities_very_unhealthy += 1
            elif r.aqi >= 151:
                summary.cities_unhealthy += 1
            elif r.aqi >= 101:
                summary.cities_unhealthy_sensitive += 1

            if r.aqi > summary.worst_aqi:
                summary.worst_aqi = r.aqi
                summary.worst_city = f"{r.city}, {r.state}"
                summary.worst_pollutant = r.pollutant

            if r.is_wildfire_smoke and r.region_id:
                if r.region_id not in summary.affected_regions:
                    summary.affected_regions.append(r.region_id)

        # Smoke event = 3+ cities with PM2.5 AQI > 100
        smoke_cities = sum(1 for r in pm25_readings if r.is_wildfire_smoke)
        summary.smoke_event_detected = smoke_cities >= 3

        return summary

    async def close(self):
        await self._client.aclose()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_aqi_category(aqi: int) -> dict:
    """Return category info dict for a given AQI value."""
    for (low, high), info in AQI_CATEGORIES.items():
        if low <= aqi <= high:
            return info
    if aqi > 500:
        return {"label": "Hazardous", "color": "maroon",
                "health": "Extremely hazardous conditions."}
    return {"label": "Good", "color": "green", "health": "Air quality is satisfactory."}


def _aqi_to_alert_tier(aqi: int) -> str:
    """Map AQI value to WildfireNet alert tier."""
    if aqi >= 201:
        return "EMERGENCY"
    elif aqi >= 151:
        return "WARNING"
    elif aqi >= 101:
        return "ADVISORY"
    elif aqi >= 51:
        return "WATCH"
    return "NONE"


# ── CLI Entry Point ───────────────────────────────────────────────────────────

async def main():
    """
    Quick test — run directly to check current AQI in Michigan.
    Usage: python src/detection/airnow_client.py
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold red]🌫️  WildfireNet — AirNow PM2.5 Test[/bold red]\n")

    try:
        client = AirNowClient()
    except ValueError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        return

    console.print("[yellow]Querying PM2.5 / AQI across all monitored cities...[/yellow]\n")

    summary = await client.get_smoke_event_summary()

    # Print smoke event status
    if summary.smoke_event_detected:
        console.print("[bold red]🚨 SMOKE EVENT DETECTED — Regional wildfire smoke confirmed[/bold red]")
    else:
        console.print("[green]✓ No regional smoke event detected[/green]")

    console.print(f"\nWorst city: [red]{summary.worst_city}[/red] — AQI {summary.worst_aqi}")
    console.print(f"Cities hazardous:        [red]{summary.cities_hazardous}[/red]")
    console.print(f"Cities very unhealthy:   [red]{summary.cities_very_unhealthy}[/red]")
    console.print(f"Cities unhealthy:        [yellow]{summary.cities_unhealthy}[/yellow]")
    console.print(f"Cities unhealthy (sens): [yellow]{summary.cities_unhealthy_sensitive}[/yellow]")
    console.print(f"Affected regions: {summary.affected_regions}\n")

    if summary.readings:
        table = Table(title="PM2.5 AQI by City")
        table.add_column("City", style="cyan")
        table.add_column("State")
        table.add_column("AQI", style="bold")
        table.add_column("Category")
        table.add_column("Smoke?", style="red")
        table.add_column("Alert Tier", style="bold red")

        for r in sorted(summary.readings, key=lambda x: x.aqi, reverse=True):
            color = {
                "Good": "green", "Moderate": "yellow",
                "Unhealthy for Sensitive": "orange",
                "Unhealthy": "red", "Very Unhealthy": "magenta",
                "Hazardous": "bold red",
            }.get(r.category, "white")

            table.add_row(
                r.city, r.state,
                f"[{color}]{r.aqi}[/{color}]",
                f"[{color}]{r.category}[/{color}]",
                "🔥 YES" if r.is_wildfire_smoke else "No",
                r.alert_tier,
            )
        console.print(table)

    await client.close()
    console.print("\n[green]✓ AirNow client test complete.[/green]\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())