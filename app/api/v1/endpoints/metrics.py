from fastapi import APIRouter
from prometheus_client.registry import REGISTRY

router = APIRouter()

def parse_prometheus_metric(metric_name: str):
    """Helper to parse a metric from the registry by name."""
    for metric in REGISTRY.collect():
        if metric.name == metric_name:
            samples = metric.samples
            if not samples:
                return None
            
            if metric.type in ('gauge', 'counter'):
                return sum(s.value for s in samples)
                
            if metric.type == 'histogram':
                total_sum = None
                total_count = None
                for s in samples:
                    if s.name.endswith('_sum'):
                        total_sum = s.value
                    if s.name.endswith('_count'):
                        total_count = s.value
                if total_sum is not None and total_count is not None and total_count > 0:
                    return total_sum / total_count
    return None


def get_latency_health(latency_seconds: float, yellow_threshold: float, red_threshold: float) -> str:
    """Determines health status based on latency and thresholds."""
    if latency_seconds is None:
        return "unknown"
    if latency_seconds > red_threshold:
        return "red"
    if latency_seconds > yellow_threshold:
        return "yellow"
    return "green"

@router.get("/summary")
async def get_metrics_summary():
    """
    Returns a JSON summary of key operational metrics.
    These metrics are global and reflect the state of the service since the last restart.
    """
    avg_api_latency = parse_prometheus_metric("ttl_api_request_duration_seconds")
    avg_db_latency = parse_prometheus_metric("ttl_db_query_duration_seconds")
    avg_redis_latency = parse_prometheus_metric("ttl_redis_command_duration_seconds")

    summary = {
        "total_checks": int(parse_prometheus_metric("ttl_checks_total") or 0),
        "total_notifications_sent": int(parse_prometheus_metric("ttl_notifications_sent_total") or 0),
        "average_api_latency_seconds": avg_api_latency,
        "average_db_latency_seconds": avg_db_latency,
        "average_redis_latency_seconds": avg_redis_latency,
        "total_api_requests": int(parse_prometheus_metric("ttl_api_requests_total") or 0),
        "health": {
            "api_latency": get_latency_health(avg_api_latency, yellow_threshold=0.5, red_threshold=1.0),
            "db_latency": get_latency_health(avg_db_latency, yellow_threshold=0.1, red_threshold=0.5),
            "redis_latency": get_latency_health(avg_redis_latency, yellow_threshold=0.01, red_threshold=0.1),
        }
    }
            
    return summary
