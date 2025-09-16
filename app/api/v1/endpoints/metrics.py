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


@router.get("/summary")
async def get_metrics_summary():
    """
    Returns a JSON summary of key operational metrics.
    These metrics are global and reflect the state of the service since the last restart.
    """
    summary = {
        "total_checks": int(parse_prometheus_metric("ttl_checks_total") or 0),
        "total_notifications_sent": int(parse_prometheus_metric("ttl_notifications_sent_total") or 0),
        "average_api_latency_seconds": parse_prometheus_metric("ttl_api_request_duration_seconds"),
        "total_api_requests": int(parse_prometheus_metric("ttl_api_requests_total") or 0),
    }
            
    return summary
