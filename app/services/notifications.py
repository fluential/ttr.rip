from app.worker import send_telegram_notification_task

def format_duration(seconds: int) -> str:
    """Formats seconds into a human-readable string like '1m 30s'."""
    if seconds < 0:
        return "N/A"
    if seconds < 60:
        return f"{seconds}s"
    
    minutes = seconds // 60
    secs = seconds % 60
    
    return f"{minutes}m {secs}s"

def schedule_telegram_notification(check_id: int, message: str):
    """Enqueues a task to send a Telegram notification."""
    send_telegram_notification_task.delay(check_id, message)
