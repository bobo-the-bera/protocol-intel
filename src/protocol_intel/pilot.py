"""Postgres owns the one-shot trial clock; process restarts cannot extend it."""

from protocol_intel.database import Database


async def pilot_status(db: Database, start_hours: int = 0) -> dict:
    if start_hours:
        # Only the first actual cycle establishes the deadline, using the database clock.
        await db.execute(
            """
          INSERT INTO pilot_window(singleton,started_at,ends_at)
          VALUES(true,now(),now()+make_interval(hours=>:hours)) ON CONFLICT DO NOTHING
        """,
            hours=start_hours,
        )
    rows = await db.rows("""
      SELECT started_at,ends_at,
        GREATEST(0,extract(epoch FROM ends_at-clock_timestamp()))::float AS remaining_seconds
      FROM pilot_window WHERE singleton
    """)
    if not rows:
        return {"started": False, "expired": False}
    row = rows[0]
    return {
        "started": True,
        "started_at": row["started_at"].isoformat(),
        "ends_at": row["ends_at"].isoformat(),
        "remaining_seconds": row["remaining_seconds"],
        "expired": row["remaining_seconds"] <= 0,
    }
