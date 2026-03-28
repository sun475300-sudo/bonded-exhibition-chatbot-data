"""Add performance indexes.

Adds indexes on commonly queried columns across all tables.
"""

VERSION = 2
NAME = "add_indexes"


def up(conn):
    """Create performance indexes."""
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_chat_logs_timestamp
            ON chat_logs (timestamp);
        CREATE INDEX IF NOT EXISTS idx_chat_logs_category
            ON chat_logs (category);
        CREATE INDEX IF NOT EXISTS idx_chat_logs_faq_id
            ON chat_logs (faq_id);

        CREATE INDEX IF NOT EXISTS idx_feedback_query_id
            ON feedback (query_id);
        CREATE INDEX IF NOT EXISTS idx_feedback_timestamp
            ON feedback (timestamp);

        CREATE INDEX IF NOT EXISTS idx_faq_candidates_status
            ON faq_candidates (status);

        CREATE INDEX IF NOT EXISTS idx_delivery_sub
            ON delivery_log (subscription_id);
        CREATE INDEX IF NOT EXISTS idx_delivery_created
            ON delivery_log (created_at);

        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_logs (timestamp);
        CREATE INDEX IF NOT EXISTS idx_audit_actor
            ON audit_logs (actor);
        CREATE INDEX IF NOT EXISTS idx_audit_resource
            ON audit_logs (resource_type, resource_id);

        CREATE INDEX IF NOT EXISTS idx_alerts_severity
            ON alerts (severity);
        CREATE INDEX IF NOT EXISTS idx_alerts_created
            ON alerts (created_at);

        CREATE INDEX IF NOT EXISTS idx_satisfaction_session
            ON satisfaction (session_id);
    """)


def down(conn):
    """Drop all performance indexes."""
    conn.executescript("""
        DROP INDEX IF EXISTS idx_chat_logs_timestamp;
        DROP INDEX IF EXISTS idx_chat_logs_category;
        DROP INDEX IF EXISTS idx_chat_logs_faq_id;
        DROP INDEX IF EXISTS idx_feedback_query_id;
        DROP INDEX IF EXISTS idx_feedback_timestamp;
        DROP INDEX IF EXISTS idx_faq_candidates_status;
        DROP INDEX IF EXISTS idx_delivery_sub;
        DROP INDEX IF EXISTS idx_delivery_created;
        DROP INDEX IF EXISTS idx_audit_timestamp;
        DROP INDEX IF EXISTS idx_audit_actor;
        DROP INDEX IF EXISTS idx_audit_resource;
        DROP INDEX IF EXISTS idx_alerts_severity;
        DROP INDEX IF EXISTS idx_alerts_created;
        DROP INDEX IF EXISTS idx_satisfaction_session;
    """)
