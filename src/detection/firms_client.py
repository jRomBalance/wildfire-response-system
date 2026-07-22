"""
WildfireNet — NASA FIRMS Satellite Fire Detection Client
=========================================================
Pulls real-time active fire data from NASA FIRMS API.
Sources: VIIRS (375m), MODIS (1km), GOES (near real-time)

API Docs: https://firms.modaps.eosdis.nasa.gov/api/
Get your free key: https://firms.modaps.eosdis.nasa.gov/api/map_key/
"""

import os
import csv
import logging
import httpx
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional
from io import StringIO
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Satellite sources available from FIRMS
SOURCES = {
    "VIIRS_SNPP":   "VIIRS_SNPP_NRT",    # 375m resolution, near real-time
    "VIIRS_NOAA20": "VIIRS_NOAA20_NRT",  # 375m resolution, near real-time
    "VIIRS_NOAA21": "VIIRS_NOAA21_NRT",  # 375m resolution, near real-time
    "MODIS":        "MODIS_NRT",          # 1km resolution, near real-time
}

# Confidence thresholds — only alert on high-confidence detections
CONFIDENCE_MAP = {
    "high": 3,
    "nominal": 2,
    "low": 1,
}

# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class FireDetection:
    """A single fire detection event from satellite data."""
    source: str                    # Which satellite/sensor
    latitude: float
    longitude: float
    brightness: float              # Brightness temperature (Kelvin)
    frp: float                     # Fire Radiative Power (MW) — fire intensity
    confidence: str                # low / nominal / high
    confidence_score: int          # 1=low, 2=nominal, 3=high
    acq_date: str                  # Acquisition date (YYYY-MM-DD)
    acq_time: str                  # Acquisition time (HHMM UTC)
    satellite: str                 # Satellite name
    instrument: str                # Sensor instrument
    daynight: str                  # D=day, N=night
    detected_at: str               # ISO timestamp
    region_id: Optional[str] = None  # Matched priority region (if any)
    severity_score: int = 0        # 0–5 computed severity

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FIRMSQueryResult:
    """Result of a FIRMS API query."""
    source: str
    bbox: dict
    days: int
    detections: list[FireDetection] = field(default_factory=list)
    total_count: int = 0
    high_confidence_count: int = 0
    queried_at: str = ""
    error: Optional[str] = None

    def summary(self) -> str:
        return (
            f"[{self.source}] {self.total_count} detections "
            f"({self.high_confidence_count} high-confidence) "
            f"in bbox {self.bbox} over {self.days} day(s)"
        )


# ── Priority Regions (matches data/regions/priority_regions.json) ─────────────

PRIORITY_REGIONS = [
    # Priority 1 - Active/Critical
    {"id": "ontario-michigan-border",     "north": 47.5, "south": 45.5, "east": -82.0,  "west": -87.0},
    {"id": "northern-ontario-boreal",     "north": 54.0, "south": 46.0, "east": -79.0,  "west": -95.0},
    {"id": "upper-peninsula-michigan",    "north": 47.5, "south": 45.8, "east": -83.5,  "west": -90.5},
    {"id": "alberta-bc-interior",         "north": 58.0, "south": 50.0, "east": -110.0, "west": -122.0},
    {"id": "saskatchewan-boreal",         "north": 60.0, "south": 50.0, "east": -98.0,  "west": -110.0},
    {"id": "pacific-northwest",           "north": 49.0, "south": 44.0, "east": -116.0, "west": -124.5},
    {"id": "northern-rockies",            "north": 49.0, "south": 44.0, "east": -110.0, "west": -117.5},
    # Priority 2 - High Risk
    {"id": "northern-minnesota-bwca",     "north": 49.0, "south": 46.5, "east": -89.5,  "west": -94.0},
    {"id": "new-mexico-arizona-highlands","north": 37.0, "south": 31.0, "east": -103.0, "west": -114.0},
    {"id": "quebec-boreal",               "north": 58.0, "south": 47.0, "east": -64.0,  "west": -80.0},
    {"id": "great-lakes-national-forests","north": 47.5, "south": 42.5, "east": -83.0,  "west": -92.0},
    {"id": "nevada-utah-great-basin",     "north": 42.0, "south": 37.0, "east": -111.0, "west": -120.0},
    {"id": "colorado-rockies",            "north": 41.0, "south": 37.0, "east": -102.0, "west": -109.5},
    {"id": "wyoming-forests",             "north": 45.0, "south": 41.0, "east": -104.0, "west": -111.0},
    {"id": "wisconsin-upper-midwest",     "north": 47.0, "south": 44.0, "east": -86.5,  "west": -92.5},
    {"id": "southern-bc-okanagan",        "north": 51.0, "south": 48.5, "east": -117.0, "west": -121.0},
    {"id": "california-northern",         "north": 42.0, "south": 38.5, "east": -119.5, "west": -124.5},
    {"id": "california-southern",         "north": 36.0, "south": 32.5, "east": -114.5, "west": -120.5},
    # Priority 3 - Monitoring
    {"id": "texas-hill-country",          "north": 36.5, "south": 29.0, "east": -94.0,  "west": -103.0},
    {"id": "appalachian-southeast",       "north": 38.0, "south": 33.0, "east": -79.0,  "west": -87.0},
    {"id": "alaska-interior",             "north": 68.0, "south": 61.0, "east": -141.0, "west": -165.0},
    {"id": "manitoba-boreal",             "north": 58.0, "south": 50.0, "east": -89.0,  "west": -102.0},
]


# ── Core Client ──────────────────────────────────────────────────────────────

class FIRMSClient:
    """
    Async client for NASA FIRMS fire detection API.

    Usage:
        client = FIRMSClient()
        result = await client.query_region(
            north=47.5, south=45.5, east=-82.0, west=-87.0,
            days=1, source="VIIRS_SNPP"
        )
        for fire in result.detections:
            print(fire.latitude, fire.longitude, fire.frp, fire.confidence)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("NASA_FIRMS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "NASA FIRMS API key required.\n"
                "Get yours free at: https://firms.modaps.eosdis.nasa.gov/api/map_key/\n"
                "Then set NASA_FIRMS_API_KEY in your .env file."
            )
        self.base_url = FIRMS_BASE_URL
        self._client = httpx.AsyncClient(timeout=30.0)

    async def query_region(
        self,
        north: float,
        south: float,
        east: float,
        west: float,
        days: int = 1,
        source: str = "VIIRS_SNPP",
        min_confidence: str = "nominal",
    ) -> FIRMSQueryResult:
        """
        Query FIRMS for fire detections in a bounding box.

        Args:
            north/south/east/west: Bounding box coordinates (decimal degrees)
            days: Number of days back to query (1–10)
            source: Satellite source key (see SOURCES dict)
            min_confidence: Minimum confidence level to include ('low','nominal','high')

        Returns:
            FIRMSQueryResult with list of FireDetection objects
        """
        source_id = SOURCES.get(source, source)
        bbox_str = f"{west},{south},{east},{north}"
        url = f"{self.base_url}/{self.api_key}/{source_id}/{bbox_str}/{days}"

        bbox = {"north": north, "south": south, "east": east, "west": west}
        result = FIRMSQueryResult(
            source=source,
            bbox=bbox,
            days=days,
            queried_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(f"Querying FIRMS [{source}] bbox={bbox_str} days={days}")

        try:
            response = await self._client.get(url)
            response.raise_for_status()

            detections = self._parse_csv(
                response.text, source, min_confidence
            )
            result.detections = detections
            result.total_count = len(detections)
            result.high_confidence_count = sum(
                1 for d in detections if d.confidence == "high"
            )

            logger.info(result.summary())

        except httpx.HTTPStatusError as e:
            msg = f"FIRMS API HTTP error: {e.response.status_code} — {e.response.text}"
            logger.error(msg)
            result.error = msg
        except httpx.RequestError as e:
            msg = f"FIRMS API request failed: {e}"
            logger.error(msg)
            result.error = msg
        except Exception as e:
            msg = f"Unexpected error querying FIRMS: {e}"
            logger.error(msg)
            result.error = msg

        return result

    def _parse_csv(
        self,
        csv_text: str,
        source: str,
        min_confidence: str,
    ) -> list[FireDetection]:
        """Parse FIRMS CSV response into FireDetection objects."""
        detections = []
        min_score = CONFIDENCE_MAP.get(min_confidence, 2)

        if not csv_text.strip() or csv_text.startswith("Error"):
            logger.warning(f"FIRMS returned no data or error: {csv_text[:200]}")
            return detections

        reader = csv.DictReader(StringIO(csv_text))

        for row in reader:
            try:
                # VIIRS uses 'confidence' as text: low/nominal/high
                # MODIS uses 'confidence' as integer 0–100
                raw_conf = row.get("confidence", "nominal").strip().lower()

                if raw_conf.isdigit():
                    # MODIS integer confidence → map to text
                    conf_int = int(raw_conf)
                    if conf_int >= 80:
                        confidence = "high"
                    elif conf_int >= 30:
                        confidence = "nominal"
                    else:
                        confidence = "low"
                else:
                    confidence = raw_conf if raw_conf in CONFIDENCE_MAP else "nominal"

                conf_score = CONFIDENCE_MAP.get(confidence, 2)

                # Filter by minimum confidence
                if conf_score < min_score:
                    continue

                lat = float(row.get("latitude", 0))
                lon = float(row.get("longitude", 0))
                brightness = float(row.get("bright_ti4") or row.get("brightness") or 0)
                frp = float(row.get("frp", 0))
                acq_date = row.get("acq_date", "")
                acq_time = row.get("acq_time", "0000")
                satellite = row.get("satellite", source)
                instrument = row.get("instrument", "")
                daynight = row.get("daynight", "D")

                # Build ISO timestamp
                try:
                    dt_str = f"{acq_date} {acq_time.zfill(4)}"
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H%M")
                    detected_at = dt.replace(tzinfo=timezone.utc).isoformat()
                except Exception:
                    detected_at = datetime.now(timezone.utc).isoformat()

                # Compute severity score (0–5) based on FRP
                severity = self._compute_severity(frp, confidence)

                # Match to priority region
                region_id = self._match_region(lat, lon)

                detection = FireDetection(
                    source=source,
                    latitude=lat,
                    longitude=lon,
                    brightness=brightness,
                    frp=frp,
                    confidence=confidence,
                    confidence_score=conf_score,
                    acq_date=acq_date,
                    acq_time=acq_time,
                    satellite=satellite,
                    instrument=instrument,
                    daynight=daynight,
                    detected_at=detected_at,
                    region_id=region_id,
                    severity_score=severity,
                )
                detections.append(detection)

            except (ValueError, KeyError) as e:
                logger.debug(f"Skipping malformed row: {row} — {e}")
                continue

        return detections

    def _compute_severity(self, frp: float, confidence: str) -> int:
        """
        Compute severity score 0–5 based on Fire Radiative Power (MW).
        FRP is the best single indicator of fire intensity from satellite.

        Scale:
            0 = No fire / noise
            1 = Very small fire (<5 MW)
            2 = Small fire (5–50 MW)
            3 = Moderate fire (50–500 MW)
            4 = Large fire (500–2000 MW)
            5 = Megafire (>2000 MW)
        """
        if frp <= 0:
            return 0
        if frp < 5:
            score = 1
        elif frp < 50:
            score = 2
        elif frp < 500:
            score = 3
        elif frp < 2000:
            score = 4
        else:
            score = 5

        # Downgrade by 1 if low confidence
        if confidence == "low" and score > 0:
            score = max(0, score - 1)

        return score

    def _match_region(self, lat: float, lon: float) -> Optional[str]:
        """Check if a detection falls within a priority region."""
        for region in PRIORITY_REGIONS:
            if (region["south"] <= lat <= region["north"] and
                    region["west"] <= lon <= region["east"]):
                return region["id"]
        return None

    async def query_all_priority_regions(
        self,
        days: int = 1,
        source: str = "VIIRS_SNPP",
        min_confidence: str = "nominal",
    ) -> list[FIRMSQueryResult]:
        """
        Query all priority regions concurrently.
        Returns list of results, one per region.
        """
        tasks = [
            self.query_region(
                north=r["north"], south=r["south"],
                east=r["east"], west=r["west"],
                days=days, source=source,
                min_confidence=min_confidence,
            )
            for r in PRIORITY_REGIONS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Region query failed: {r}")
            else:
                valid.append(r)
        return valid

    async def get_active_fires_summary(self, days: int = 1) -> dict:
        """
        High-level summary of active fires across all priority regions.
        Queries VIIRS (best resolution) across all regions.
        """
        results = await self.query_all_priority_regions(
            days=days, source="VIIRS_SNPP", min_confidence="nominal"
        )

        all_detections = []
        for r in results:
            all_detections.extend(r.detections)

        # Group by region
        by_region = {}
        for d in all_detections:
            rid = d.region_id or "unknown"
            if rid not in by_region:
                by_region[rid] = []
            by_region[rid].append(d)

        # Find highest severity per region
        region_summaries = {}
        for rid, fires in by_region.items():
            max_severity = max(f.severity_score for f in fires)
            max_frp = max(f.frp for f in fires)
            high_conf = sum(1 for f in fires if f.confidence == "high")
            region_summaries[rid] = {
                "detection_count": len(fires),
                "high_confidence_count": high_conf,
                "max_severity": max_severity,
                "max_frp_mw": round(max_frp, 1),
                "alert_tier": _severity_to_tier(max_severity),
            }

        return {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "total_detections": len(all_detections),
            "regions_with_fire": len(by_region),
            "by_region": region_summaries,
        }

    async def close(self):
        await self._client.aclose()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _severity_to_tier(severity: int) -> str:
    """Map severity score to alert tier."""
    if severity == 0:
        return "NONE"
    elif severity == 1:
        return "WATCH"
    elif severity == 2:
        return "ADVISORY"
    elif severity == 3:
        return "WARNING"
    else:
        return "EMERGENCY"


# ── CLI Entry Point ───────────────────────────────────────────────────────────

async def main():
    """
    Quick test — run directly to verify your FIRMS API key works.
    Usage: python src/detection/firms_client.py
    """
    import json
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold red]🔥 WildfireNet — FIRMS Detection Test[/bold red]\n")

    try:
        client = FIRMSClient()
    except ValueError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        return

    # Test: query Ontario-Michigan border (active emergency as of July 2026)
    console.print("[yellow]Querying Ontario–Michigan border zone (VIIRS, last 1 day)...[/yellow]")

    result = await client.query_region(
        north=47.5, south=45.5, east=-82.0, west=-87.0,
        days=1, source="VIIRS_SNPP", min_confidence="nominal"
    )

    if result.error:
        console.print(f"[red]Query failed: {result.error}[/red]")
        await client.close()
        return

    console.print(f"\n[green]✓ {result.summary()}[/green]\n")

    if result.detections:
        table = Table(title="Active Fire Detections")
        table.add_column("Lat", style="cyan")
        table.add_column("Lon", style="cyan")
        table.add_column("FRP (MW)", style="magenta")
        table.add_column("Confidence", style="yellow")
        table.add_column("Severity", style="red")
        table.add_column("Tier", style="bold red")
        table.add_column("Day/Night")
        table.add_column("Region")

        for d in sorted(result.detections, key=lambda x: x.frp, reverse=True)[:20]:
            table.add_row(
                str(d.latitude),
                str(d.longitude),
                str(d.frp),
                d.confidence,
                str(d.severity_score),
                _severity_to_tier(d.severity_score),
                d.daynight,
                d.region_id or "—",
            )
        console.print(table)
    else:
        console.print("[dim]No detections in this region for the query period.[/dim]")

    # Full summary across all priority regions
    console.print("\n[yellow]Running full priority region scan...[/yellow]")
    summary = await client.get_active_fires_summary(days=1)
    console.print(f"\n[bold]Active Fire Summary — All Priority Regions[/bold]")
    console.print(f"Total detections: [red]{summary['total_detections']}[/red]")
    console.print(f"Regions with fire: [red]{summary['regions_with_fire']}[/red]\n")

    for region_id, data in summary["by_region"].items():
        tier_color = {
            "EMERGENCY": "bold red",
            "WARNING": "red",
            "ADVISORY": "yellow",
            "WATCH": "green",
            "NONE": "dim",
        }.get(data["alert_tier"], "white")

        console.print(
            f"  [{tier_color}]{data['alert_tier']:10}[/{tier_color}] "
            f"{region_id:35} "
            f"{data['detection_count']:4} detections  "
            f"Max FRP: {data['max_frp_mw']:8.1f} MW"
        )

    await client.close()
    console.print("\n[green]✓ FIRMS client test complete.[/green]\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())