"""
WildfireNet — Alert Dispatcher
================================
Core routing logic: takes a fire event or AQI reading,
determines alert tier, and fans out to all notification channels
(SMS, email, webhook/CAD) simultaneously.

Alert Tiers:
    WATCH     → Single anomaly, unconfirmed. Log only.
    ADVISORY  → 2+ sensors or camera confirmation. Alert local station.
    WARNING   → AI confirms fire, <10 acres estimated. Full regional alert + drone.
    EMERGENCY → >10 acres OR rapid spread. All channels + cross-border + public AQI.
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# ── Alert Tier Definitions ────────────────────────────────────────────────────

ALERT_TIERS = {
    "WATCH": {
        "level": 1,
        "description": "Single anomaly detected, unconfirmed",
        "actions": ["log"],
        "notify_firefighters": False,
        "launch_drone": False,
        "public_broadcast": False,
        "cross_border": False,
    },
    "ADVISORY": {
        "level": 2,
        "description": "Multiple sensors or camera confirmation",
        "actions": ["log", "email"],
        "notify_firefighters": True,
        "launch_drone": False,
        "public_broadcast": False,
        "cross_border": False,
    },
    "WARNING": {
        "level": 3,
        "description": "Fire confirmed by AI fusion, estimated <10 acres",
        "actions": ["log", "sms", "email", "webhook"],
        "notify_firefighters": True,
        "launch_drone": True,
        "public_broadcast": False,
        "cross_border": False,
    },
    "EMERGENCY": {
        "level": 4,
        "description": "Large fire or rapid spread detected",
        "actions": ["log", "sms", "email", "webhook", "public"],
        "notify_firefighters": True,
        "launch_drone": True,
        "public_broadcast": True,
        "cross_border": True,
    },
}


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class FireAlert:
    """A fire alert event to be dispatched."""
    alert_id: str
    tier: str                          # WATCH / ADVISORY / WARNING / EMERGENCY
    source: str                        # firms_satellite / iot_sensor / camera / fusion
    latitude: float
    longitude: float
    region_id: Optional[str]
    region_name: Optional[str]
    severity_score: int                # 0–5
    frp_mw: Optional[float]           # Fire Radiative Power (satellite)
    aqi_pm25: Optional[int]           # PM2.5 AQI (if AQI-triggered)
    confidence: str                    # low / nominal / high
    description: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dispatched_channels: list[str] = field(default_factory=list)
    drone_dispatched: bool = False
    cross_border_notified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def short_summary(self) -> str:
        return (
            f"[{self.tier}] Fire detected at {self.latitude:.3f},{self.longitude:.3f} "
            f"| Region: {self.region_id or 'unknown'} "
            f"| Severity: {self.severity_score}/5 "
            f"| Source: {self.source}"
        )


@dataclass
class DispatchResult:
    """Result of dispatching an alert across all channels."""
    alert_id: str
    tier: str
    channels_attempted: list[str] = field(default_factory=list)
    channels_succeeded: list[str] = field(default_factory=list)
    channels_failed: list[str] = field(default_factory=list)
    drone_dispatched: bool = False
    errors: list[str] = field(default_factory=list)
    dispatched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def success(self) -> bool:
        return len(self.channels_succeeded) > 0

    def summary(self) -> str:
        return (
            f"Alert {self.alert_id} [{self.tier}]: "
            f"{len(self.channels_succeeded)}/{len(self.channels_attempted)} channels OK "
            f"| Drone: {'YES' if self.drone_dispatched else 'NO'}"
        )


# ── Alert Dispatcher ──────────────────────────────────────────────────────────

class AlertDispatcher:
    """
    Central alert routing engine.

    Takes a FireAlert, determines which channels to activate based on tier,
    and fans out notifications concurrently.

    Usage:
        dispatcher = AlertDispatcher()
        alert = FireAlert(
            alert_id="alert-001",
            tier="WARNING",
            source="firms_satellite",
            latitude=46.5, longitude=-84.5,
            region_id="ontario-michigan-border",
            region_name="Ontario–Michigan Border Zone",
            severity_score=3,
            frp_mw=125.0,
            aqi_pm25=None,
            confidence="high",
            description="High-confidence fire detection, FRP 125 MW"
        )
        result = await dispatcher.dispatch(alert)
    """

    def __init__(self):
        # Import notifiers lazily to avoid import errors if keys not set
        self._sms = None
        self._email = None
        self._webhook_url = os.getenv("ALERT_WEBHOOK_URL")

        # Firefighter contact registry (in production: load from DB)
        self._firefighter_contacts = self._load_firefighter_contacts()

    def _load_firefighter_contacts(self) -> list[dict]:
        """
        Load firefighter contact list.
        In production: query database by region.
        For now: environment-configured test contacts.
        """
        contacts = []
        # Support comma-separated list of phone numbers in env
        phones = os.getenv("FIREFIGHTER_PHONES", "")
        emails = os.getenv("FIREFIGHTER_EMAILS", "")

        for phone in phones.split(","):
            phone = phone.strip()
            if phone:
                contacts.append({"type": "phone", "value": phone, "name": "Firefighter Unit"})

        for email in emails.split(","):
            email = email.strip()
            if email:
                contacts.append({"type": "email", "value": email, "name": "Fire Station"})

        if not contacts:
            logger.warning(
                "No firefighter contacts configured. "
                "Set FIREFIGHTER_PHONES and FIREFIGHTER_EMAILS in .env"
            )
        return contacts

    async def dispatch(self, alert: FireAlert) -> DispatchResult:
        """
        Main dispatch method. Routes alert to all appropriate channels
        based on tier configuration.
        """
        tier_config = ALERT_TIERS.get(alert.tier, ALERT_TIERS["WATCH"])
        result = DispatchResult(alert_id=alert.alert_id, tier=alert.tier)

        logger.info(f"Dispatching: {alert.short_summary()}")

        # Always log
        self._log_alert(alert)
        result.channels_attempted.append("log")
        result.channels_succeeded.append("log")

        # Build concurrent tasks based on tier
        tasks = []

        if "sms" in tier_config["actions"] and tier_config["notify_firefighters"]:
            tasks.append(("sms", self._send_sms_alerts(alert)))

        if "email" in tier_config["actions"]:
            tasks.append(("email", self._send_email_alerts(alert)))

        if "webhook" in tier_config["actions"] and self._webhook_url:
            tasks.append(("webhook", self._send_webhook(alert)))

        if "public" in tier_config["actions"]:
            tasks.append(("public_broadcast", self._send_public_broadcast(alert)))

        if tier_config["launch_drone"]:
            tasks.append(("drone", self._dispatch_drone(alert)))

        if tier_config["cross_border"]:
            tasks.append(("cross_border", self._notify_cross_border(alert)))

        # Execute all channels concurrently
        if tasks:
            channel_names = [t[0] for t in tasks]
            coroutines = [t[1] for t in tasks]
            result.channels_attempted.extend(channel_names)

            outcomes = await asyncio.gather(*coroutines, return_exceptions=True)

            for channel, outcome in zip(channel_names, outcomes):
                if isinstance(outcome, Exception):
                    logger.error(f"Channel [{channel}] failed: {outcome}")
                    result.channels_failed.append(channel)
                    result.errors.append(f"{channel}: {str(outcome)}")
                elif outcome is True:
                    result.channels_succeeded.append(channel)
                    if channel == "drone":
                        result.drone_dispatched = True
                else:
                    result.channels_failed.append(channel)

        logger.info(result.summary())
        return result

    def _log_alert(self, alert: FireAlert):
        """Structured log entry for every alert."""
        level = {
            "WATCH": logging.DEBUG,
            "ADVISORY": logging.INFO,
            "WARNING": logging.WARNING,
            "EMERGENCY": logging.CRITICAL,
        }.get(alert.tier, logging.INFO)

        logger.log(level, f"🔥 ALERT {alert.tier}: {alert.description} | "
                          f"lat={alert.latitude} lon={alert.longitude} | "
                          f"region={alert.region_id} | severity={alert.severity_score}/5 | "
                          f"source={alert.source} | id={alert.alert_id}")

    async def _send_sms_alerts(self, alert: FireAlert) -> bool:
        """Send SMS to all firefighter phone contacts in the affected region."""
        try:
            from src.alerts.sms_notifier import SMSNotifier
            notifier = SMSNotifier()

            phone_contacts = [
                c for c in self._firefighter_contacts
                if c["type"] == "phone"
            ]

            if not phone_contacts:
                logger.warning("No phone contacts configured — SMS skipped")
                return False

            message = _build_sms_message(alert)
            results = await asyncio.gather(*[
                notifier.send(to=c["value"], message=message)
                for c in phone_contacts
            ], return_exceptions=True)

            success_count = sum(1 for r in results if r is True)
            logger.info(f"SMS: {success_count}/{len(phone_contacts)} sent")
            return success_count > 0

        except ImportError:
            logger.warning("SMS notifier not available — install twilio")
            return False
        except Exception as e:
            logger.error(f"SMS dispatch failed: {e}")
            return False

    async def _send_email_alerts(self, alert: FireAlert) -> bool:
        """Send email alerts to fire station contacts."""
        try:
            from src.alerts.email_notifier import EmailNotifier
            notifier = EmailNotifier()

            email_contacts = [
                c for c in self._firefighter_contacts
                if c["type"] == "email"
            ]

            if not email_contacts:
                logger.warning("No email contacts configured — email skipped")
                return False

            subject, body = _build_email_message(alert)
            results = await asyncio.gather(*[
                notifier.send(
                    to=c["value"],
                    subject=subject,
                    body=body,
                    name=c.get("name", "Fire Station"),
                )
                for c in email_contacts
            ], return_exceptions=True)

            success_count = sum(1 for r in results if r is True)
            logger.info(f"Email: {success_count}/{len(email_contacts)} sent")
            return success_count > 0

        except ImportError:
            logger.warning("Email notifier not available — install sendgrid")
            return False
        except Exception as e:
            logger.error(f"Email dispatch failed: {e}")
            return False

    async def _send_webhook(self, alert: FireAlert) -> bool:
        """
        POST alert payload to CAD system webhook.
        Most fire dispatch centers accept webhook/REST integrations.
        """
        import httpx
        try:
            payload = {
                "event_type": "wildfire_alert",
                "alert_id": alert.alert_id,
                "tier": alert.tier,
                "latitude": alert.latitude,
                "longitude": alert.longitude,
                "region_id": alert.region_id,
                "severity": alert.severity_score,
                "description": alert.description,
                "source": alert.source,
                "timestamp": alert.created_at,
                "maps_link": (
                    f"https://www.google.com/maps?q={alert.latitude},{alert.longitude}"
                ),
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                logger.info(f"Webhook delivered: {response.status_code}")
                return True

        except Exception as e:
            logger.error(f"Webhook failed: {e}")
            return False

    async def _send_public_broadcast(self, alert: FireAlert) -> bool:
        """
        Broadcast public AQI/smoke warning.
        In production: integrate with WEA (Wireless Emergency Alerts)
        and state emergency management systems.
        Currently: logs the broadcast message.
        """
        message = (
            f"🚨 AIR QUALITY EMERGENCY — {alert.region_name or alert.region_id}\n"
            f"Wildfire smoke causing hazardous PM2.5 levels.\n"
            f"Stay indoors. Close windows. Use air purifier.\n"
            f"Sensitive groups: children, elderly, respiratory conditions.\n"
            f"Monitor: airnow.gov | Updated: {alert.created_at}"
        )
        logger.critical(f"PUBLIC BROADCAST:\n{message}")
        # TODO: Integrate WEA API, state emergency management webhook
        return True

    async def _dispatch_drone(self, alert: FireAlert) -> bool:
        """
        Trigger autonomous drone response via Dryad Silvaguard API.
        Observation drone launches first → confirms fire → suppression drone.
        """
        try:
            from src.drones.drone_dispatcher import DroneDispatcher
            dispatcher = DroneDispatcher()
            result = await dispatcher.dispatch_to_location(
                lat=alert.latitude,
                lon=alert.longitude,
                alert_tier=alert.tier,
                alert_id=alert.alert_id,
            )
            logger.info(f"Drone dispatch: {result}")
            return result

        except ImportError:
            logger.warning("Drone dispatcher not available")
            return False
        except Exception as e:
            logger.error(f"Drone dispatch failed: {e}")
            return False

    async def _notify_cross_border(self, alert: FireAlert) -> bool:
        """
        Notify cross-border agencies (CIFFC Canada ↔ USFS).
        In production: CIFFC API + USFS ROSS system integration.
        Currently: logs the cross-border notification.
        """
        logger.critical(
            f"CROSS-BORDER ALERT → CIFFC Canada + USFS: "
            f"Fire at {alert.latitude:.3f},{alert.longitude:.3f} | "
            f"Region: {alert.region_id} | Tier: {alert.tier}"
        )
        # TODO: CIFFC API integration (https://www.ciffc.ca/)
        # TODO: USFS ROSS (Resource Ordering and Status System) integration
        return True


# ── Message Builders ──────────────────────────────────────────────────────────

def _build_sms_message(alert: FireAlert) -> str:
    """Build concise SMS message for firefighter units."""
    tier_emoji = {
        "WATCH": "👀", "ADVISORY": "⚠️",
        "WARNING": "🔥", "EMERGENCY": "🚨"
    }.get(alert.tier, "🔥")

    lines = [
        f"{tier_emoji} WILDFIRENET {alert.tier}",
        f"Region: {alert.region_name or alert.region_id or 'Unknown'}",
        f"GPS: {alert.latitude:.4f}, {alert.longitude:.4f}",
        f"Severity: {alert.severity_score}/5 | Source: {alert.source}",
    ]
    if alert.frp_mw:
        lines.append(f"Fire intensity: {alert.frp_mw:.0f} MW")
    if alert.aqi_pm25:
        lines.append(f"PM2.5 AQI: {alert.aqi_pm25}")

    lines.append(f"Maps: maps.google.com/?q={alert.latitude},{alert.longitude}")
    lines.append(f"ID: {alert.alert_id}")

    return "\n".join(lines)


def _build_email_message(alert: FireAlert) -> tuple[str, str]:
    """Build email subject and HTML body for fire station alerts."""
    tier_colors = {
        "WATCH": "#2196F3", "ADVISORY": "#FF9800",
        "WARNING": "#FF5722", "EMERGENCY": "#B71C1C"
    }
    color = tier_colors.get(alert.tier, "#FF5722")

    subject = f"[WildfireNet {alert.tier}] Fire detected — {alert.region_name or alert.region_id}"

    body = f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px;">
    <div style="background:{color}; color:white; padding:20px; border-radius:8px 8px 0 0;">
        <h1 style="margin:0;">🔥 WildfireNet {alert.tier}</h1>
        <p style="margin:5px 0 0;">{ALERT_TIERS[alert.tier]['description']}</p>
    </div>
    <div style="border:2px solid {color}; padding:20px; border-radius:0 0 8px 8px;">
        <table style="width:100%; border-collapse:collapse;">
            <tr><td style="padding:8px; font-weight:bold;">Region</td>
                <td style="padding:8px;">{alert.region_name or alert.region_id or 'Unknown'}</td></tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:8px; font-weight:bold;">Coordinates</td>
                <td style="padding:8px;">{alert.latitude:.5f}, {alert.longitude:.5f}</td></tr>
            <tr><td style="padding:8px; font-weight:bold;">Severity</td>
                <td style="padding:8px;">{alert.severity_score}/5</td></tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:8px; font-weight:bold;">Detection Source</td>
                <td style="padding:8px;">{alert.source}</td></tr>
            <tr><td style="padding:8px; font-weight:bold;">Confidence</td>
                <td style="padding:8px;">{alert.confidence}</td></tr>
            {"<tr style='background:#f5f5f5;'><td style='padding:8px; font-weight:bold;'>Fire Intensity</td><td style='padding:8px;'>" + str(alert.frp_mw) + " MW (Fire Radiative Power)</td></tr>" if alert.frp_mw else ""}
            {"<tr><td style='padding:8px; font-weight:bold;'>PM2.5 AQI</td><td style='padding:8px;'>" + str(alert.aqi_pm25) + "</td></tr>" if alert.aqi_pm25 else ""}
            <tr style="background:#f5f5f5;">
                <td style="padding:8px; font-weight:bold;">Alert ID</td>
                <td style="padding:8px; font-family:monospace;">{alert.alert_id}</td></tr>
            <tr><td style="padding:8px; font-weight:bold;">Detected At</td>
                <td style="padding:8px;">{alert.created_at}</td></tr>
        </table>

        <div style="margin-top:20px;">
            <a href="https://www.google.com/maps?q={alert.latitude},{alert.longitude}"
               style="background:{color}; color:white; padding:12px 24px;
                      text-decoration:none; border-radius:4px; font-weight:bold;">
                📍 View on Map
            </a>
            &nbsp;&nbsp;
            <a href="https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{alert.longitude},{alert.latitude},8z"
               style="background:#333; color:white; padding:12px 24px;
                      text-decoration:none; border-radius:4px; font-weight:bold;">
                🛰️ NASA FIRMS Live Map
            </a>
        </div>

        <p style="margin-top:20px; color:#666; font-size:12px;">
            This alert was generated automatically by WildfireNet.<br>
            Description: {alert.description}
        </p>
    </div>
    </body></html>
    """
    return subject, body


# ── Factory: Build Alert from FIRMS Detection ─────────────────────────────────

def build_alert_from_firms(detection, region_name: Optional[str] = None) -> FireAlert:
    """
    Convert a FIRMSFireDetection object into a FireAlert.
    Import from firms_client and pass detection objects here.
    """
    import uuid
    tier = {0: "NONE", 1: "WATCH", 2: "ADVISORY", 3: "WARNING", 4: "EMERGENCY", 5: "EMERGENCY"}
    return FireAlert(
        alert_id=f"firms-{uuid.uuid4().hex[:8]}",
        tier=tier.get(detection.severity_score, "WATCH"),
        source="firms_satellite",
        latitude=detection.latitude,
        longitude=detection.longitude,
        region_id=detection.region_id,
        region_name=region_name,
        severity_score=detection.severity_score,
        frp_mw=detection.frp,
        aqi_pm25=None,
        confidence=detection.confidence,
        description=(
            f"Satellite fire detection via {detection.source}. "
            f"FRP: {detection.frp:.1f} MW. "
            f"Confidence: {detection.confidence}. "
            f"Acquired: {detection.acq_date} {detection.acq_time} UTC."
        ),
    )


def build_alert_from_aqi(reading, region_name: Optional[str] = None) -> FireAlert:
    """
    Convert an AQIReading object into a FireAlert.
    Used when PM2.5 AQI crosses emergency thresholds.
    """
    import uuid
    return FireAlert(
        alert_id=f"aqi-{uuid.uuid4().hex[:8]}",
        tier=reading.alert_tier,
        source="epa_airnow",
        latitude=reading.latitude,
        longitude=reading.longitude,
        region_id=reading.region_id,
        region_name=region_name or f"{reading.city}, {reading.state}",
        severity_score=min(5, reading.aqi // 100),
        frp_mw=None,
        aqi_pm25=reading.aqi,
        confidence="high",
        description=(
            f"PM2.5 AQI {reading.aqi} ({reading.category}) in {reading.city}, {reading.state}. "
            f"Likely wildfire smoke. Health: {reading.health_message}"
        ),
    )


# ── CLI Test ──────────────────────────────────────────────────────────────────

async def main():
    """Test the alert dispatcher with a simulated fire event."""
    from rich.console import Console
    import uuid

    console = Console()
    console.print("\n[bold red]🚨 WildfireNet — Alert Dispatcher Test[/bold red]\n")

    dispatcher = AlertDispatcher()

    # Simulate a WARNING-tier fire detection at Ontario-Michigan border
    test_alert = FireAlert(
        alert_id=f"test-{uuid.uuid4().hex[:8]}",
        tier="WARNING",
        source="firms_satellite",
        latitude=46.512,
        longitude=-84.337,
        region_id="ontario-michigan-border",
        region_name="Ontario–Michigan Border Zone",
        severity_score=3,
        frp_mw=187.5,
        aqi_pm25=None,
        confidence="high",
        description="Test alert: High-confidence VIIRS fire detection. FRP 187.5 MW.",
    )

    console.print(f"[yellow]Dispatching test alert: {test_alert.short_summary()}[/yellow]\n")

    result = await dispatcher.dispatch(test_alert)

    console.print(f"\n[bold]Dispatch Result:[/bold]")
    console.print(f"  Success:    [green]{result.success}[/green]")
    console.print(f"  Attempted:  {result.channels_attempted}")
    console.print(f"  Succeeded:  [green]{result.channels_succeeded}[/green]")
    console.print(f"  Failed:     [red]{result.channels_failed}[/red]")
    console.print(f"  Drone:      {'[green]YES[/green]' if result.drone_dispatched else '[dim]NO[/dim]'}")
    if result.errors:
        console.print(f"  Errors:     [red]{result.errors}[/red]")

    console.print(f"\n[green]✓ Dispatcher test complete.[/green]\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())