"""Error messages must remain useful without printing credentials or bot URLs."""

import os
import re


def safe_error(exc: BaseException) -> str:
    message = str(exc)
    for key in ("DATABASE_URL", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        value = os.getenv(key)
        if value:
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot[REDACTED]", message)
    # Dotenv values are not necessarily present in os.environ. Cover connection strings too.
    message = re.sub(r"postgresql(?:\+psycopg)?://[^\s]+", "postgresql://[REDACTED]", message)
    message = re.sub(r"https?://[^\s]*:[^\s]*@", "https://[REDACTED]@", message)
    return f"{type(exc).__name__}: {message[:700]}"
