"""Initial schema migration.

Creates all existing tables: chat_logs, feedback, faq_candidates,
subscriptions, delivery_log, tenants, audit_logs, alerts, satisfaction,
faq_history, law_versions, update_notifications.
"""

VERSION = 1
NAME = "initial_schema"


def up(conn):
    """Create all base tables."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            query TEXT NOT NULL,
            category TEXT,
            faq_id TEXT,
            is_escalation INTEGER NOT NULL DEFAULT 0,
            response_preview TEXT
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            rating TEXT NOT NULL,
            comment TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS faq_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggested_question TEXT NOT NULL,
            suggested_category TEXT DEFAULT '',
            similar_queries TEXT DEFAULT '[]',
            frequency INTEGER DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            events TEXT NOT NULL,
            secret TEXT,
            created_at REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS delivery_log (
            id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            url TEXT NOT NULL,
            request_payload TEXT,
            response_status INTEGER,
            response_body TEXT,
            success INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            completed_at REAL
        );

        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT,
            details TEXT,
            ip_address TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            metadata TEXT,
            is_read INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS satisfaction (
            session_id TEXT,
            query TEXT,
            response_type TEXT,
            re_asked BOOLEAN DEFAULT 0,
            feedback TEXT DEFAULT 'none',
            timestamp TEXT
        );

        CREATE TABLE IF NOT EXISTS faq_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faq_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_data TEXT,
            new_data TEXT,
            timestamp TEXT NOT NULL,
            user TEXT DEFAULT 'admin'
        );

        CREATE TABLE IF NOT EXISTS law_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_name TEXT NOT NULL,
            article TEXT NOT NULL,
            version_date TEXT NOT NULL,
            previous_text TEXT,
            current_text TEXT NOT NULL,
            detected_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS update_notifications (
            id TEXT PRIMARY KEY,
            faq_id TEXT NOT NULL,
            affected_field TEXT NOT NULL,
            reason TEXT NOT NULL,
            law_name TEXT,
            article TEXT,
            created_at TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0,
            acknowledged_at TEXT
        );
    """)


def down(conn):
    """Drop all base tables."""
    conn.executescript("""
        DROP TABLE IF EXISTS update_notifications;
        DROP TABLE IF EXISTS law_versions;
        DROP TABLE IF EXISTS faq_history;
        DROP TABLE IF EXISTS satisfaction;
        DROP TABLE IF EXISTS alerts;
        DROP TABLE IF EXISTS audit_logs;
        DROP TABLE IF EXISTS tenants;
        DROP TABLE IF EXISTS delivery_log;
        DROP TABLE IF EXISTS subscriptions;
        DROP TABLE IF EXISTS faq_candidates;
        DROP TABLE IF EXISTS feedback;
        DROP TABLE IF EXISTS chat_logs;
    """)
