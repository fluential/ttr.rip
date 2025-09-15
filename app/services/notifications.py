import httpx
from app.db.models import Check

def format_duration(seconds: int) -> str:
    """Formats seconds into a human-readable string like '1m 30s'."""
    if seconds < 0:
        return "N/A"
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    secs = seconds % 60
    
    return f"{minutes}m {secs}s"


async def send_telegram_notification(check: Check, message: str):
    """Sends a notification to the configured Telegram chat."""
    if not all([check.telegram_enabled, check.telegram_bot_token, check.telegram_chat_id]):
        return

    url = f"https://api.telegram.org/bot{check.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": check.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            print(f"Successfully sent Telegram notification for check '{check.name}' (ID: {check.id})")
        except httpx.HTTPStatusError as e:
            print(f"Error sending Telegram notification for check '{check.name}' (ID: {check.id}): {e.response.status_code} {e.response.text}")
        except Exception as e:
            print(f"An unexpected error occurred while sending Telegram notification for check '{check.name}' (ID: {check.id}): {e}")
