"""
WildfireNet - Database Layer
=============================
Supports both MySQL (production) and SQLite (local dev).
Auto-detects based on DB_TYPE environment variable.

MySQL env vars (set in Railway):
    DB_TYPE=mysql
    DB_HOST=sh00125.bluehost.com
    DB_PORT=3306
    DB_NAME=jhueydmy_wildfirenet
    DB_USER=jhueydmy_wfn_user
    DB_PASSWORD=your_password

SQLite env vars (local dev):
    DB_PATH=wildfirenet.db  (default)
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()
DB_PATH = os.getenv("DB_PATH", "wildfirenet.db")


# ── Connection ────────────────────────────────────────────────────────────────

def get_connection():
    """Get a database connection — MySQL or SQLite based on DB_TYPE."""
    if DB_TYPE == "mysql":
        import pymysql
        import pymysql.cursors
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "wildfirenet"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=10,
        )
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


def _placeholder():
    """Return correct placeholder for current DB type."""
    return "%s" if DB_TYPE == "mysql" else "?"


def _execute(conn, sql, params=None):
    """Execute SQL with correct placeholder style."""
    if DB_TYPE == "mysql":
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur
    else:
        return conn.execute(sql, params or ())


def _fetchone(conn, sql, params=None):
    """Fetch one row."""
    if DB_TYPE == "mysql":
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()
    else:
        return conn.execute(sql, params or ()).fetchone()


def _fetchall(conn, sql, params=None):
    """Fetch all rows."""
    if DB_TYPE == "mysql":
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    else:
        return conn.execute(sql, params or ()).fetchall()


# ── Database Init ─────────────────────────────────────────────────────────────

def init_db():
    """Initialize all tables. Safe to call multiple times."""
    conn = get_connection()
    try:
        if DB_TYPE == "mysql":
            _init_mysql(conn)
        else:
            _init_sqlite(conn)
        logger.info(f"Database initialized ({DB_TYPE})")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        raise
    finally:
        conn.close()


def _init_mysql(conn):
    """Create MySQL tables."""
    tables = [
        """CREATE TABLE IF NOT EXISTS fire_events (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            event_id        VARCHAR(32) UNIQUE NOT NULL,
            source          VARCHAR(50) NOT NULL,
            latitude        DOUBLE NOT NULL,
            longitude       DOUBLE NOT NULL,
            region_id       VARCHAR(100),
            severity_score  INT DEFAULT 0,
            frp_mw          DOUBLE,
            confidence      VARCHAR(20),
            acq_date        VARCHAR(20),
            acq_time        VARCHAR(10),
            detected_at     VARCHAR(50) NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS alerts_sent (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            alert_id        VARCHAR(50) UNIQUE NOT NULL,
            event_id        VARCHAR(32),
            tier            VARCHAR(20) NOT NULL,
            region_id       VARCHAR(100),
            latitude        DOUBLE,
            longitude       DOUBLE,
            channels        VARCHAR(200),
            drone_dispatched TINYINT DEFAULT 0,
            sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS subscribers (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            name            VARCHAR(200),
            phone           VARCHAR(20),
            email           VARCHAR(200),
            zip_code        VARCHAR(20),
            sms_consent     TINYINT DEFAULT 0,
            email_consent   TINYINT DEFAULT 0,
            opted_in_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            opted_out_at    TIMESTAMP NULL,
            is_active       TINYINT DEFAULT 1,
            opt_in_method   VARCHAR(50) DEFAULT 'web_form',
            UNIQUE KEY unique_phone (phone),
            UNIQUE KEY unique_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS subscriber_regions (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            subscriber_id   INT NOT NULL,
            region_id       VARCHAR(100) NOT NULL,
            added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_sub_region (subscriber_id, region_id),
            FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS manage_tokens (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            email       VARCHAR(200) NOT NULL,
            token       VARCHAR(100) UNIQUE NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  VARCHAR(50) NOT NULL,
            used        TINYINT DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS aqi_readings (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            city            VARCHAR(100) NOT NULL,
            state           VARCHAR(10),
            zip_code        VARCHAR(20),
            pollutant       VARCHAR(20),
            aqi             INT,
            category        VARCHAR(50),
            is_wildfire_smoke TINYINT DEFAULT 0,
            region_id       VARCHAR(100),
            recorded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_fire_region ON fire_events(region_id, detected_at)",
        "CREATE INDEX IF NOT EXISTS idx_fire_severity ON fire_events(severity_score)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_region ON alerts_sent(region_id, sent_at)",
        "CREATE INDEX IF NOT EXISTS idx_sub_regions ON subscriber_regions(region_id)",
        "CREATE INDEX IF NOT EXISTS idx_subs_active ON subscribers(is_active)",
    ]

    with conn.cursor() as cur:
        for sql in tables:
            cur.execute(sql)
        # MySQL uses CREATE INDEX differently - skip IF NOT EXISTS
        for sql in indexes:
            try:
                cur.execute(sql.replace("IF NOT EXISTS ", ""))
            except Exception:
                pass  # Index already exists
    conn.commit()


def _init_sqlite(conn):
    """Create SQLite tables."""
    conn.executescript("""
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
            sent_at         TEXT DEFAULT (datetime('now'))
        );
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
        CREATE TABLE IF NOT EXISTS subscriber_regions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id   INTEGER NOT NULL,
            region_id       TEXT NOT NULL,
            added_at        TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
            UNIQUE(subscriber_id, region_id)
        );
        CREATE TABLE IF NOT EXISTS manage_tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            token       TEXT UNIQUE NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            expires_at  TEXT NOT NULL,
            used        INTEGER DEFAULT 0
        );
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
        CREATE INDEX IF NOT EXISTS idx_fire_region ON fire_events(region_id, detected_at);
        CREATE INDEX IF NOT EXISTS idx_fire_severity ON fire_events(severity_score);
        CREATE INDEX IF NOT EXISTS idx_alerts_region ON alerts_sent(region_id, sent_at);
        CREATE INDEX IF NOT EXISTS idx_sub_regions ON subscriber_regions(region_id);
        CREATE INDEX IF NOT EXISTS idx_subs_active ON subscribers(is_active);
    """)
    conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row):
    """Convert a DB row to dict regardless of DB type."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(row)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _now_mysql():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Duplicate Detection ───────────────────────────────────────────────────────

def was_recently_alerted(region_id: str, severity_score: int, hours: int = 2) -> bool:
    """Check if we already sent an alert for this region recently."""
    conn = get_connection()
    try:
        p = _placeholder()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours))
        if DB_TYPE == "mysql":
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
            sql = f"""
                SELECT COUNT(*) as cnt FROM alerts_sent
                WHERE region_id = {p} AND sent_at > {p}
            """
            row = _fetchone(conn, sql, (region_id, cutoff_str))
        else:
            cutoff_str = cutoff.isoformat()
            sql = f"""
                SELECT COUNT(*) as cnt FROM alerts_sent
                WHERE region_id = {p} AND sent_at > {p}
            """
            row = _fetchone(conn, sql, (region_id, cutoff_str))

        count = row["cnt"] if row else 0
        if count > 0:
            logger.debug(f"Duplicate suppressed: {region_id} alerted {count}x in last {hours}h")
        return count > 0
    finally:
        conn.close()


# ── Fire Events ───────────────────────────────────────────────────────────────

def save_fire_event(detection) -> str:
    """Save a fire detection. Returns event_id."""
    import hashlib
    event_id = hashlib.md5(
        f"{detection.source}_{detection.latitude:.3f}_{detection.longitude:.3f}"
        f"_{detection.acq_date}_{detection.acq_time}".encode()
    ).hexdigest()[:16]

    conn = get_connection()
    try:
        p = _placeholder()
        if DB_TYPE == "mysql":
            sql = f"""INSERT IGNORE INTO fire_events
                (event_id, source, latitude, longitude, region_id,
                 severity_score, frp_mw, confidence, acq_date, acq_time, detected_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})"""
        else:
            sql = f"""INSERT OR IGNORE INTO fire_events
                (event_id, source, latitude, longitude, region_id,
                 severity_score, frp_mw, confidence, acq_date, acq_time, detected_at)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})"""

        _execute(conn, sql, (
            event_id, detection.source, detection.latitude, detection.longitude,
            detection.region_id, detection.severity_score, detection.frp,
            detection.confidence, detection.acq_date, detection.acq_time,
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
        p = _placeholder()
        channels = ",".join(getattr(alert, "dispatched_channels", []))
        drone = 1 if getattr(alert, "drone_dispatched", False) else 0

        if DB_TYPE == "mysql":
            sql = f"""INSERT IGNORE INTO alerts_sent
                (alert_id, event_id, tier, region_id, latitude, longitude, channels, drone_dispatched)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p})"""
        else:
            sql = f"""INSERT OR IGNORE INTO alerts_sent
                (alert_id, event_id, tier, region_id, latitude, longitude, channels, drone_dispatched)
                VALUES ({p},{p},{p},{p},{p},{p},{p},{p})"""

        _execute(conn, sql, (
            alert.alert_id, event_id, alert.tier, alert.region_id,
            alert.latitude, alert.longitude, channels, drone,
        ))
        conn.commit()
        logger.info(f"Alert recorded: {alert.alert_id} [{alert.tier}]")
    finally:
        conn.close()


# ── Subscribers ───────────────────────────────────────────────────────────────

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
    """Add or update a subscriber and add their region."""
    if not phone and not email:
        return {"success": False, "message": "Phone or email required"}

    conn = get_connection()
    try:
        p = _placeholder()
        sms = 1 if sms_consent else 0
        eml = 1 if email_consent else 0

        if DB_TYPE == "mysql":
            sql = f"""INSERT INTO subscribers
                (name, phone, email, zip_code, sms_consent, email_consent, opt_in_method)
                VALUES ({p},{p},{p},{p},{p},{p},{p})
                ON DUPLICATE KEY UPDATE
                    name=VALUES(name), email=COALESCE(VALUES(email), email),
                    sms_consent=VALUES(sms_consent), email_consent=VALUES(email_consent),
                    is_active=1, opted_out_at=NULL"""
        else:
            sql = f"""INSERT INTO subscribers
                (name, phone, email, zip_code, sms_consent, email_consent, opt_in_method)
                VALUES ({p},{p},{p},{p},{p},{p},{p})
                ON CONFLICT(phone) DO UPDATE SET
                    name=excluded.name, email=COALESCE(excluded.email, email),
                    sms_consent=excluded.sms_consent, email_consent=excluded.email_consent,
                    is_active=1, opted_out_at=NULL"""

        _execute(conn, sql, (name, phone, email, zip_code, sms, eml, opt_in_method))
        conn.commit()

        # Get subscriber ID
        if phone:
            row = _fetchone(conn, f"SELECT id FROM subscribers WHERE phone={p}", (phone,))
        else:
            row = _fetchone(conn, f"SELECT id FROM subscribers WHERE email={p}", (email,))

        if not row:
            return {"success": False, "message": "Failed to create subscriber"}

        sub_id = _row_to_dict(row)["id"]

        # Add region
        if region_id:
            if DB_TYPE == "mysql":
                try:
                    _execute(conn, f"INSERT IGNORE INTO subscriber_regions (subscriber_id, region_id) VALUES ({p},{p})",
                             (sub_id, region_id))
                except Exception:
                    pass
            else:
                _execute(conn, f"INSERT OR IGNORE INTO subscriber_regions (subscriber_id, region_id) VALUES ({p},{p})",
                         (sub_id, region_id))
            conn.commit()

        logger.info(f"Subscriber upserted: {name} | region: {region_id}")
        return {"success": True, "message": f"Welcome to WildfireNet, {name}!"}

    except Exception as e:
        logger.error(f"Subscriber add failed: {e}")
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def opt_out_subscriber(phone: str) -> bool:
    """Process STOP - mark subscriber inactive."""
    conn = get_connection()
    try:
        p = _placeholder()
        row = _fetchone(conn, f"SELECT id FROM subscribers WHERE phone={p}", (phone,))
        if not row:
            return False
        sub_id = _row_to_dict(row)["id"]
        if DB_TYPE == "mysql":
            _execute(conn, f"UPDATE subscribers SET is_active=0, opted_out_at=NOW() WHERE id={p}", (sub_id,))
        else:
            _execute(conn, f"UPDATE subscribers SET is_active=0, opted_out_at=datetime('now') WHERE id={p}", (sub_id,))
        _execute(conn, f"DELETE FROM subscriber_regions WHERE subscriber_id={p}", (sub_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def opt_out_by_email(email: str) -> bool:
    """Opt out by email address."""
    conn = get_connection()
    try:
        p = _placeholder()
        row = _fetchone(conn, f"SELECT id FROM subscribers WHERE email={p}", (email,))
        if not row:
            return False
        sub_id = _row_to_dict(row)["id"]
        if DB_TYPE == "mysql":
            _execute(conn, f"UPDATE subscribers SET is_active=0, opted_out_at=NOW() WHERE id={p}", (sub_id,))
        else:
            _execute(conn, f"UPDATE subscribers SET is_active=0, opted_out_at=datetime('now') WHERE id={p}", (sub_id,))
        _execute(conn, f"DELETE FROM subscriber_regions WHERE subscriber_id={p}", (sub_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def get_subscribers_for_region(region_id: str) -> list:
    """Get all active subscribers watching a specific region."""
    conn = get_connection()
    try:
        p = _placeholder()
        sql = f"""
            SELECT s.name, s.phone, s.email, s.sms_consent, s.email_consent
            FROM subscribers s
            JOIN subscriber_regions sr ON s.id = sr.subscriber_id
            WHERE sr.region_id = {p} AND s.is_active = 1
        """
        rows = _fetchall(conn, sql, (region_id,))
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_all_active_subscribers() -> list:
    """Get all active subscribers with their watched regions."""
    conn = get_connection()
    try:
        if DB_TYPE == "mysql":
            sql = """
                SELECT s.name, s.phone, s.email, s.sms_consent, s.email_consent,
                       GROUP_CONCAT(sr.region_id ORDER BY sr.region_id SEPARATOR ', ') as regions
                FROM subscribers s
                LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
                WHERE s.is_active = 1
                GROUP BY s.id
                ORDER BY s.opted_in_at DESC
            """
        else:
            sql = """
                SELECT s.name, s.phone, s.email, s.sms_consent, s.email_consent,
                       GROUP_CONCAT(sr.region_id, ', ') as regions
                FROM subscribers s
                LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
                WHERE s.is_active = 1
                GROUP BY s.id
                ORDER BY s.opted_in_at DESC
            """
        rows = _fetchall(conn, sql)
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_subscriber_by_contact(phone: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
    """Look up a subscriber by phone or email."""
    conn = get_connection()
    try:
        p = _placeholder()
        if DB_TYPE == "mysql":
            group_concat = "GROUP_CONCAT(sr.region_id ORDER BY sr.region_id SEPARATOR ',') as regions"
        else:
            group_concat = "GROUP_CONCAT(sr.region_id, ',') as regions"

        if phone:
            sql = f"""SELECT s.*, {group_concat}
                FROM subscribers s
                LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
                WHERE s.phone = {p} AND s.is_active = 1 GROUP BY s.id"""
            row = _fetchone(conn, sql, (phone,))
        elif email:
            sql = f"""SELECT s.*, {group_concat}
                FROM subscribers s
                LEFT JOIN subscriber_regions sr ON s.id = sr.subscriber_id
                WHERE s.email = {p} AND s.is_active = 1 GROUP BY s.id"""
            row = _fetchone(conn, sql, (email,))
        else:
            return None
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_subscriber_regions(email: str) -> list:
    """Get all regions a subscriber is watching."""
    conn = get_connection()
    try:
        p = _placeholder()
        row = _fetchone(conn, f"SELECT id FROM subscribers WHERE email={p} AND is_active=1", (email,))
        if not row:
            return []
        sub_id = _row_to_dict(row)["id"]
        rows = _fetchall(conn, f"SELECT region_id FROM subscriber_regions WHERE subscriber_id={p}", (sub_id,))
        return [_row_to_dict(r)["region_id"] for r in rows]
    finally:
        conn.close()


def update_subscriber_regions(email: str, new_regions: list) -> bool:
    """Replace all regions for a subscriber."""
    conn = get_connection()
    try:
        p = _placeholder()
        row = _fetchone(conn, f"SELECT id FROM subscribers WHERE email={p} AND is_active=1", (email,))
        if not row:
            return False
        sub_id = _row_to_dict(row)["id"]
        _execute(conn, f"DELETE FROM subscriber_regions WHERE subscriber_id={p}", (sub_id,))
        for region_id in new_regions:
            if DB_TYPE == "mysql":
                try:
                    _execute(conn, f"INSERT IGNORE INTO subscriber_regions (subscriber_id, region_id) VALUES ({p},{p})",
                             (sub_id, region_id))
                except Exception:
                    pass
            else:
                _execute(conn, f"INSERT OR IGNORE INTO subscriber_regions (subscriber_id, region_id) VALUES ({p},{p})",
                         (sub_id, region_id))
        conn.commit()
        logger.info(f"Updated regions for {email}: {new_regions}")
        return True
    except Exception as e:
        logger.error(f"Region update failed: {e}")
        return False
    finally:
        conn.close()


# ── Manage Tokens ─────────────────────────────────────────────────────────────

def create_manage_token(email: str) -> Optional[str]:
    """Generate a magic link token. Valid 24 hours."""
    import secrets
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    conn = get_connection()
    try:
        p = _placeholder()
        _execute(conn, f"UPDATE manage_tokens SET used=1 WHERE email={p}", (email,))
        _execute(conn, f"INSERT INTO manage_tokens (email, token, expires_at) VALUES ({p},{p},{p})",
                 (email, token, expires))
        conn.commit()
        return token
    except Exception as e:
        logger.error(f"Token creation failed: {e}")
        return None
    finally:
        conn.close()


def validate_manage_token(token: str) -> Optional[str]:
    """Validate a magic link token. Returns email if valid."""
    conn = get_connection()
    try:
        p = _placeholder()
        row = _fetchone(conn, f"SELECT email, expires_at, used FROM manage_tokens WHERE token={p}", (token,))
        if not row:
            return None
        row = _row_to_dict(row)
        if row["used"]:
            return None
        expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            return None
        return row["email"]
    finally:
        conn.close()


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_fire_summary(days: int = 7) -> dict:
    """Get fire event summary for the last N days."""
    conn = get_connection()
    try:
        p = _placeholder()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days))
        if DB_TYPE == "mysql":
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        else:
            cutoff_str = cutoff.isoformat()

        total_row = _fetchone(conn, f"SELECT COUNT(*) as n FROM fire_events WHERE created_at > {p}", (cutoff_str,))
        total = _row_to_dict(total_row)["n"] if total_row else 0

        by_region = _fetchall(conn, f"""
            SELECT region_id, COUNT(*) as count,
                   MAX(severity_score) as max_severity, MAX(frp_mw) as max_frp
            FROM fire_events WHERE created_at > {p}
            GROUP BY region_id ORDER BY max_severity DESC
        """, (cutoff_str,))

        alerts_row = _fetchone(conn, f"SELECT COUNT(*) as n FROM alerts_sent WHERE sent_at > {p}", (cutoff_str,))
        alerts_sent = _row_to_dict(alerts_row)["n"] if alerts_row else 0

        return {
            "period_days": days,
            "total_detections": total,
            "alerts_sent": alerts_sent,
            "by_region": [_row_to_dict(r) for r in by_region],
        }
    finally:
        conn.close()


def save_aqi_reading(reading) -> None:
    """Save an AQI reading."""
    conn = get_connection()
    try:
        p = _placeholder()
        _execute(conn, f"""INSERT INTO aqi_readings
            (city, state, zip_code, pollutant, aqi, category, is_wildfire_smoke, region_id)
            VALUES ({p},{p},{p},{p},{p},{p},{p},{p})""",
            (reading.city, reading.state, None, reading.pollutant, reading.aqi,
             reading.category, 1 if reading.is_wildfire_smoke else 0, reading.region_id))
        conn.commit()
    finally:
        conn.close()