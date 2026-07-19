"""
WildfireNet — SQLite Fire Event Database
==========================================
Tracks fire detections and alerts to prevent duplicate notifications.
Uses SQLite — zero cost, zero setup, built into Python.

Tables:
    fire_events   — every detection from NASA FIRMS / IoT / camera
    alerts_sent   — every alert dispatched (prevents duplicates)
    subscribers   — opt-in contacts for SMS/email alerts
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "wildfirenet.db")


# ── Database Setup ────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
    return conn


def init_db():
    """
    Initialize all database tables.
    Safe to call multiple times — uses IF NOT EXISTS.
    """
    conn = get_connection()
    try:
        conn.executescript("""
            -- Fire detection events from all sources
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

            -- Alerts that have been dispatched
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

            -- Subscriber opt-in registry
            CREATE TABLE IF NOT EXISTS subscribers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT,
                phone           TEXT UNIQUE,
                email           TEXT UNIQUE,
                region_id       TEXT,
                zip_code        TEXT,
                sms_consent     INTEGER DEFAULT 0,
                email_consent   INTEGER DEFAULT 0,
                opted_in_at     TEXT DEFAULT (datetime('now')),
                opted_out_at    TEXT,
                is_active       INTEGER DEFAULT 1,
                opt_in_method   TEXT DEFAULT 'web_form'
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

            -- Indexes for fast lookups
            CREATE INDEX IF NOT EXISTS idx_fire_events_region
                ON fire_events(region_id, detected_at);
            CREATE INDEX IF NOT EXISTS idx_fire_events_severity
                ON fire_events(severity_score);
            CREATE INDEX IF NOT EXISTS idx_alerts_sent_region
                ON alerts_sent(region_id, sent_at);
            CREATE INDEX IF NOT EXISTS idx_subscribers_region
                ON subscribers(region_id, is_active);
        """)
        conn.commit()
        logger.info(f"Database initialized: {DB_PATH}")
    finally:
        conn.close()


# ── Duplicate Detection ───────────────────────────────────────────────────────

def was_recently_alerted(
    region_id: str,
    severity_score: int,
    hours: int = 2,
) -> bool:
    """
    Check if we already sent an alert for this region at this severity
    within the last N hours. Prevents alert spam.

    Args:
        region_id:      Region to check
        severity_score: Minimum severity to check for
        hours:          How far back to look (default 2 hours)

    Returns:
        True if already alerted recently (skip), False if new alert needed
    """
    conn = get_connection()
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()

        row = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM alerts_sent a
            JOIN fire_events e ON a.event_id = e.event_id
            WHERE a.region_id = ?
              AND e.severity_score >= ?
              AND a.sent_at > ?
        """, (region_id, severity_score, cutoff)).fetchone()

        count = row["cnt"] if row else 0
        if count > 0:
            logger.debug(
                f"Duplicate suppressed: {region_id} severity {severity_score} "
                f"already alerted {count}x in last {hours}h"
            )
        return count > 0

    finally:
        conn.close()


def was_detection_seen(event_id: str) -> bool:
    """Check if a specific fire detection has already been processed."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM fire_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ── Fire Event Storage ────────────────────────────────────────────────────────

def save_fire_event(detection) -> str:
    """
    Save a fire detection to the database.
    Returns the event_id.
    Skips if already exists (idempotent).
    """
    import hashlib

    # Create deterministic event ID from detection properties
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
        """, (
            event_id,
            detection.source,
            detection.latitude,
            detection.longitude,
            detection.region_id,
            detection.severity_score,
            detection.frp,
            detection.confidence,
            detection.acq_date,
            detection.acq_time,
            detection.detected_at,
        ))
        conn.commit()
        return event_id
    finally:
        conn.close()


def save_alert_sent(alert, event_id: Optional[str] = None):
    """Record that an alert was dispatched."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO alerts_sent
                (alert_id, event_id, tier, region_id, latitude, longitude,
                 channels, drone_dispatched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.alert_id,
            event_id,
            alert.tier,
            alert.region_id,
            alert.latitude,
            alert.longitude,
            ",".join(alert.dispatched_channels),
            1 if alert.drone_dispatched else 0,
        ))
        conn.commit()
        logger.info(f"Alert recorded: {alert.alert_id} [{alert.tier}]")
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
    Add a new subscriber to the alert list.
    Returns dict with success status and message.
    """
    if not phone and not email:
        return {"success": False, "message": "Phone or email required"}

    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO subscribers
                (name, phone, email, region_id, zip_code,
                 sms_consent, email_consent, opt_in_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                name=excluded.name,
                email=excluded.email,
                region_id=excluded.region_id,
                sms_consent=excluded.sms_consent,
                email_consent=excluded.email_consent,
                is_active=1,
                opted_out_at=NULL
        """, (
            name, phone, email, region_id, zip_code,
            1 if sms_consent else 0,
            1 if email_consent else 0,
            opt_in_method,
        ))
        conn.commit()
        logger.info(f"Subscriber added: {name} | {phone or email}")
        return {"success": True, "message": f"Welcome to WildfireNet, {name}!"}
    except Exception as e:
        logger.error(f"Subscriber add failed: {e}")
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def opt_out_subscriber(phone: str) -> bool:
    """Process STOP request — mark subscriber inactive."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE subscribers
            SET is_active = 0,
                opted_out_at = datetime('now')
            WHERE phone = ?
        """, (phone,))
        conn.commit()
        affected = conn.execute(
            "SELECT changes() as n"
        ).fetchone()["n"]
        logger.info(f"Subscriber opted out: {phone}")
        return affected > 0
    finally:
        conn.close()


def get_subscribers_for_region(region_id: str) -> list[dict]:
    """
    Get all active subscribers for a region.
    Used by alert dispatcher to build contact list dynamically.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT name, phone, email, sms_consent, email_consent
            FROM subscribers
            WHERE (region_id = ? OR region_id IS NULL)
              AND is_active = 1
        """, (region_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_active_subscribers() -> list[dict]:
    """Get all active subscribers across all regions."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT name, phone, email, region_id, sms_consent, email_consent
            FROM subscribers
            WHERE is_active = 1
            ORDER BY opted_in_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_fire_summary(days: int = 7) -> dict:
    """Get fire event summary for the last N days."""
    conn = get_connection()
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        total = conn.execute(
            "SELECT COUNT(*) as n FROM fire_events WHERE created_at > ?",
            (cutoff,)
        ).fetchone()["n"]

        by_region = conn.execute("""
            SELECT region_id, COUNT(*) as count,
                   MAX(severity_score) as max_severity,
                   MAX(frp_mw) as max_frp
            FROM fire_events
            WHERE created_at > ?
            GROUP BY region_id
            ORDER BY max_severity DESC
        """, (cutoff,)).fetchall()

        alerts_sent = conn.execute(
            "SELECT COUNT(*) as n FROM alerts_sent WHERE sent_at > ?",
            (cutoff,)
        ).fetchone()["n"]

        return {
            "period_days": days,
            "total_detections": total,
            "alerts_sent": alerts_sent,
            "by_region": [dict(r) for r in by_region],
        }
    finally:
        conn.close()


def save_aqi_reading(reading) -> None:
    """Save an AQI reading to the database."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO aqi_readings
                (city, state, zip_code, pollutant, aqi, category,
                 is_wildfire_smoke, region_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            reading.city, reading.state, None,
            reading.pollutant, reading.aqi, reading.category,
            1 if reading.is_wildfire_smoke else 0,
            reading.region_id,
        ))
        conn.commit()
    finally:
        conn.close()


# ── CLI Test ──────────────────────────────────────────────────────────────────

def main():
    """Initialize DB and run basic tests."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold red]🗄️  WildfireNet — SQLite Database Test[/bold red]\n")

    # Initialize
    init_db()
    console.print(f"[green]✓ Database initialized: {DB_PATH}[/green]")

    # Test subscriber
    result = add_subscriber(
        name="Test Firefighter",
        phone="+15551234567",
        email="test@wildfirenet.dev",
        region_id="ontario-michigan-border",
        zip_code="49783",
        sms_consent=True,
        email_consent=True,
        opt_in_method="test",
    )
    console.print(f"[green]✓ Subscriber test: {result['message']}[/green]")

    # Test duplicate check
    already = was_recently_alerted("ontario-michigan-border", 3, hours=2)
    console.print(f"[green]✓ Duplicate check (should be False): {already}[/green]")

    # Test summary
    summary = get_fire_summary(days=7)
    console.print(f"[green]✓ Fire summary: {summary}[/green]")

    # Show subscribers
    subs = get_all_active_subscribers()
    if subs:
        table = Table(title="Active Subscribers")
        table.add_column("Name")
        table.add_column("Phone")
        table.add_column("Email")
        table.add_column("Region")
        table.add_column("SMS")
        table.add_column("Email")
        for s in subs:
            table.add_row(
                s["name"], s["phone"] or "—", s["email"] or "—",
                s["region_id"] or "all",
                "✓" if s["sms_consent"] else "✗",
                "✓" if s["email_consent"] else "✗",
            )
        console.print(table)

    console.print("\n[green]✓ Database test complete.[/green]\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()