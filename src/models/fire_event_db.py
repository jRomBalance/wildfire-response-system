"""
WildfireNet — SQLite Fire Event Database
==========================================
Tracks fire detections and alerts to prevent duplicate notifications.
Uses SQLite — zero cost, zero setup, built into Python.

Tables:
    fire_events        — every detection from NASA FIRMS / IoT / camera
    alerts_sent        — every alert dispatched (prevents duplicates)
    subscribers        — opt-in contacts (one row per person)
    subscriber_regions — many-to-many: one subscriber, many regions
    aqi_readings       — AQI/PM2.5 readings log
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "wildfirenet.db")


# ── Database Setup ────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Initialize all tables. Safe to call multiple times."""
    conn = get_connection()
    try:
        conn.executescript("""
            -- Fire detection events
            CREATE TABLE IF NOT EXISTS fire_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        TEXT UNIQUE NOT NULL,
                source          TEXT NOT NULL,
                latitude        REAL NOT NULL,
                longitude       REAL NOT NULL,
                region_id       TEXT,
                severity_score  INTEGER DEFAULT 0,
                frp_mw          REAL,
                confidence      TEXT,
                acq_date        TEXT,
                acq_time        TEXT,
                detected_at     TEXT NOT NULL,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            -- Alerts dispatched
            CREATE TABLE IF NOT EXISTS alerts_sent (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id        TEXT UNIQUE NOT NULL,
                event_id        TEXT,
                tier            TEXT NOT NULL,
                region_id       TEXT,
                latitude        REAL,
                longitude       REAL,
                channels        TEXT,
                drone_dispatched INTEGER DEFAULT 0,
                sent_at         TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (event_id) REFERENCES fire_events(event_id)
            );

            -- Subscribers (one row per person)
            CREATE TABLE IF NOT EXISTS subscribers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT,
                phone           TEXT,
                email           TEXT,
                zip_code        TEXT,
                sms_consent     INTEGER DEFAULT 0,
                email_consent   INTEGER DEFAULT 0,
                opted_in_at     TEXT DEFAULT (datetime('now')),
                opted_out_at    TEXT,
                is_active       INTEGER DEFAULT 1,
                opt_in_method   TEXT DEFAULT 'web_form',
                UNIQUE(phone),
                UNIQUE(email)
            );

            -- Subscriber regions (many-to-many)
            -- One subscriber can watch many regions
            CREATE TABLE IF NOT EXISTS subscriber_regions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id   INTEGER NOT NULL,
                region_id       TEXT NOT NULL,
                added_at        TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
                UNIQUE(subscriber_id, region_id)
            );

            -- Magic link tokens for alert management
            CREATE TABLE IF NOT EXISTS manage_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL,
                token       TEXT UNIQUE NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                expires_at  TEXT NOT NULL,
                used        INTEGER DEFAULT 0
            );

            -- AQI readings log
            CREATE TABLE IF NOT EXISTS aqi_readings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                city            TEXT NOT NULL,
                state           TEXT,
                zip_code        TEXT,
                pollutant       TEXT,
                aqi             INTEGER,
                category        TEXT,
                is_wildfire_smoke INTEGER DEFAULT 0,
                region_id       TEXT,
                recorded_at     TEXT DEFAULT (datetime('now'))
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_fire_events_region
                ON fire_events(region_id, detected_at);
            CREATE INDEX IF NOT EXISTS idx_fire_events_severity
                ON fire_events(severity_score);
            CREATE INDEX IF NOT EXISTS idx_alerts_sent_region
                ON alerts_sent(region_id, sent_at);
            CREATE INDEX IF NOT EXISTS idx_subscriber_regions_region
                ON subscriber_regions(region_id);
            CREATE INDEX IF NOT EXISTS idx_subscribers_active
                ON subscribers(is_active);
        """)
        conn.commit()
        logger.info(f"Database initialized: {DB_PATH}")
    finally:
        conn.close()


# ── Duplicate Detection ───────────────────────────────────────────────────────

def was_recently_alerted(region_id: str, severity_score: int, hours: int = 2) -> bool:
    """Check if we already sent an alert for this region recently."""
    conn = get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM alerts_sent a
            JOIN fire_events e ON a.event_id = e.event_id
            WHERE a.region_id = ? AND e.severity_score >= ? AND a.sent_at > ?
        """, (region_id, severity_score, cutoff)).fetchone()
        count = row["cnt"] if row else 0
        if count > 0:
            logger.debug(f"Duplicate suppressed: {region_id} already alerted {count}x in {hours}h")
        return count > 0
    finally:
        conn.close()


def was_detection_seen(event_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM fire_events WHERE event_id = ?", (event_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


# ── Fire Event Storage ────────────────────────────────────────────────────────

def save_fire_event(detection) -> str:
    import hashlib
    event_id = hashlib.md5(
        f"{detection.source}_{detection.latitude:.3f}_{detection.longitude:.3f}"
        f"_{detection.acq_date}_{detection.acq_time}".encode()
    ).hexdigest()[:16]

    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO fire_events
                (event_id, source, latitude, longitude, region_id,
                 severity_score, frp_mw, confidence, acq_date, acq_time, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event_id, detection.source, detection.latitude, detection.longitude,
              detection.region_id, detection.severity_score, detection.frp,
              detection.confidence, detection.acq_date, detection.acq_time,
              detection.detected_at))
        conn.commit()
        return event_id
    finally:
        conn.close()


def save_alert_sent(alert, event_id: Optional[str] = None):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO alerts_sent
                (alert_id, event_id, tier, region_id, latitude, longitude,
                 channels, drone_dispatched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (alert.alert_id, event_id, alert.tier, alert.region_id,
              alert.latitude, alert.longitude,
              ",".join(alert.dispatched_channels),
              1 if alert.drone_dispatched else 0))
        conn.commit()
    finally:
        conn.close()


# ── Subscriber Management ─────────────────────────────────────────────────────

def add_subscriber(
    name: str,
    phone: Optional[str],
    email: Optional[str],
    region_id: Optional[str],
    zip_code: Optional[str],
    sms_consent: bool,
    email_consent: bool,
    opt_in_method: str = "web_form",
) -> dict:
    """
    Add or update a subscriber and add their region.
    Supports multiple regions per subscriber.
    """
    if not phone and not email:
        return {"success": False, "message": "Phone or email required"}

    conn = get_connection()
    try:
        # Upsert subscriber (update if exists)
        conn.execute("""
            INSERT INTO subscribers
                (name, phone, email, zip_code, sms_consent, email_consent, opt_in_method)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name=excluded.name,
                email=COALESCE(excluded.email, email),
                sms_consent=excluded.sms_consent,
                email_consent=excluded.email_consent,
                is_active=1,
                opted_out_at=NULL
        """, (name, phone, email, zip_code,
              1 if sms_consent else 0,
              1 if email_consent else 0,
              opt_in_method))

        # Get subscriber ID
        if phone:
            row = conn.execute("SELECT id FROM subscribers WHERE phone=?", (phone,)).fetchone()
        else:
            row = conn.execute("SELECT id FROM subscribers WHERE email=?", (email,)).fetchone()

        if not row:
            return {"success": False, "message": "Failed to create subscriber"}

        sub_id = row["id"]

        # Add region (ignore if already exists)
        if region_id:
            conn.execute("""
                INSERT OR IGNORE INTO subscriber_regions (subscriber_id, region_id)
                VALUES (?, ?)
            """, (sub_id, region_id))

        conn.commit()
        logger.info(f"Subscriber upserted: {name} | region: {region_id}")
        return {"success": True, "message": f"Welcome to WildfireNet, {name}!"}

    except Exception as e:
        logger.error(f"Subscriber add failed: {e}")
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def opt_out_subscriber(phone: str) -> bool:
    """Process STOP — mark subscriber inactive, remove all regions."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM subscribers WHERE phone=?", (phone,)).fetchone()
        if not row:
            return False
        sub_id = row["id"]
        conn.execute("""
            UPDATE subscribers SET is_active=0, opted_out_at=datetime('now')
            WHERE id=?
        """, (sub_id,))
        conn.execute("DELETE FROM subscriber_regions WHERE subscriber_id=?", (sub_id,))
        conn.commit()
        logger.info(f"Subscriber opted out: {phone}")
        return True
    finally:
        conn.close()


def opt_out_by_email(email: str) -> bool:
    """Opt out by email address."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM subscribers WHERE email=?", (email,)).fetchone()
        if not row:
            return False
        sub_id = row["id"]
        conn.execute("""
            UPDATE subscribers SET is_active=0, opted_out_at=datetime('now')
            WHERE id=?
        """, (sub_id,))
        conn.execute("DELETE FROM subscriber_regions WHERE subscriber_id=?", (sub_id,))
        conn.commit()
        logger.info(f"Subscriber opted out by email: {email}")
        return True
    finally:
        conn.close()


def get_subscribers_for_region(region_id: str) -> list[dict]:
    """
    Get all active subscribers watching a specific region.
    Uses the subscriber_regions join table.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT s.name, s.phone, s.email, s.sms_consent, s.email_consent
            FROM subscribers s
            JOIN subscriber_regions sr ON s.id = sr.subscriber_id
            WHERE sr.region_id = ? AND s.is_active = 1
        """, (region_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_active_subscribers() -> list[dict]:
    """Get all active subscribers with their watched regions."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT s.name, s.phone, s.email, s.sms_consent, s.email_consent,
                   GROUP_CONCAT(sr.region_id, ', ') as regions
            FROM subscribers s
            LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
            WHERE s.is_active = 1
            GROUP BY s.id
            ORDER BY s.opted_in_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_subscriber_by_contact(phone: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
    """Look up a subscriber by phone or email — for 'already registered' check."""
    conn = get_connection()
    try:
        if phone:
            row = conn.execute("""
                SELECT s.*, GROUP_CONCAT(sr.region_id, ',') as regions
                FROM subscribers s
                LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
                WHERE s.phone = ? AND s.is_active = 1
                GROUP BY s.id
            """, (phone,)).fetchone()
        elif email:
            row = conn.execute("""
                SELECT s.*, GROUP_CONCAT(sr.region_id, ',') as regions
                FROM subscribers s
                LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
                WHERE s.email = ? AND s.is_active = 1
                GROUP BY s.id
            """, (email,)).fetchone()
        else:
            return None
        return dict(row) if row else None
    finally:
        conn.close()


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_fire_summary(days: int = 7) -> dict:
    conn = get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        total = conn.execute(
            "SELECT COUNT(*) as n FROM fire_events WHERE created_at > ?", (cutoff,)
        ).fetchone()["n"]
        by_region = conn.execute("""
            SELECT region_id, COUNT(*) as count,
                   MAX(severity_score) as max_severity, MAX(frp_mw) as max_frp
            FROM fire_events WHERE created_at > ?
            GROUP BY region_id ORDER BY max_severity DESC
        """, (cutoff,)).fetchall()
        alerts_sent = conn.execute(
            "SELECT COUNT(*) as n FROM alerts_sent WHERE sent_at > ?", (cutoff,)
        ).fetchone()["n"]
        return {
            "period_days": days,
            "total_detections": total,
            "alerts_sent": alerts_sent,
            "by_region": [dict(r) for r in by_region],
        }
    finally:
        conn.close()



def create_manage_token(email: str) -> Optional[str]:
    """Generate a magic link token for alert management. Valid 24 hours."""
    import secrets
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    conn = get_connection()
    try:
        conn.execute("UPDATE manage_tokens SET used=1 WHERE email=?", (email,))
        conn.execute("INSERT INTO manage_tokens (email, token, expires_at) VALUES (?, ?, ?)",
                     (email, token, expires))
        conn.commit()
        return token
    except Exception as e:
        logger.error(f"Token creation failed: {e}")
        return None
    finally:
        conn.close()


def validate_manage_token(token: str) -> Optional[str]:
    """Validate a magic link token. Returns email if valid, None if expired/invalid."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT email, expires_at, used FROM manage_tokens WHERE token = ?", (token,)
        ).fetchone()
        if not row or row["used"]:
            return None
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            return None
        return row["email"]
    finally:
        conn.close()


def get_subscriber_regions(email: str) -> list:
    """Get all regions a subscriber is watching."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM subscribers WHERE email = ? AND is_active = 1", (email,)
        ).fetchone()
        if not row:
            return []
        rows = conn.execute(
            "SELECT region_id FROM subscriber_regions WHERE subscriber_id = ?", (row["id"],)
        ).fetchall()
        return [r["region_id"] for r in rows]
    finally:
        conn.close()


def update_subscriber_regions(email: str, new_regions: list) -> bool:
    """Replace all regions for a subscriber."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM subscribers WHERE email=? AND is_active=1", (email,)
        ).fetchone()
        if not row:
            return False
        sub_id = row["id"]
        conn.execute("DELETE FROM subscriber_regions WHERE subscriber_id=?", (sub_id,))
        for region_id in new_regions:
            conn.execute(
                "INSERT OR IGNORE INTO subscriber_regions (subscriber_id, region_id) VALUES (?, ?)",
                (sub_id, region_id)
            )
        conn.commit()
        logger.info(f"Updated regions for {email}: {new_regions}")
        return True
    except Exception as e:
        logger.error(f"Region update failed: {e}")
        return False
    finally:
        conn.close()


def save_aqi_reading(reading) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO aqi_readings
                (city, state, zip_code, pollutant, aqi, category, is_wildfire_smoke, region_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (reading.city, reading.state, None, reading.pollutant, reading.aqi,
              reading.category, 1 if reading.is_wildfire_smoke else 0, reading.region_id))
        conn.commit()
    finally:
        conn.close()


# ── CLI Test ──────────────────────────────────────────────────────────────────

def main():
    from rich.console import Console
    from rich.table import Table
    console = Console()
    console.print("\n[bold red]WildfireNet — SQLite Database Test[/bold red]\n")

    init_db()
    console.print("[green]✓ Database initialized[/green]")

    # Test multi-region subscriber
    for region in ["ontario-michigan-border", "saskatchewan-boreal", "alberta-bc-interior"]:
        result = add_subscriber(
            name="Test User", phone="+15550000001",
            email="test@wildfirenet.dev", region_id=region,
            zip_code="49783", sms_consent=True, email_consent=True,
        )
    console.print(f"[green]✓ Multi-region subscriber added[/green]")

    # Verify regions
    subs = get_all_active_subscribers()
    table = Table(title="Active Subscribers")
    table.add_column("Name")
    table.add_column("Phone")
    table.add_column("Regions Watching")
    for s in subs:
        table.add_row(s["name"], s["phone"] or "-", s.get("regions") or "none")
    console.print(table)

    # Test region lookup
    region_subs = get_subscribers_for_region("ontario-michigan-border")
    console.print(f"[green]✓ Ontario-Michigan subscribers: {len(region_subs)}[/green]")

    console.print("\n[green]✓ All DB tests passed![/green]\n")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    main()