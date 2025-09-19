from fastapi import APIRouter, Request, Response, status
from fastapi.responses import ORJSONResponse
from prometheus_client.registry import REGISTRY
import time
import hashlib

router = APIRouter()

# Simple in-process cache for summary
_SUMMARY_CACHE = {
    "ts": 0.0,
    "etag": None,
    "data": None,
}
_CACHE_TTL = 5.0

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
async def get_metrics_summary(request: Request):
    """
    Returns a JSON summary of key operational metrics.
    These metrics are global and reflect the state of the service since the last restart.
    """
    now = time.time()
    etag_header = request.headers.get("if-none-match")

    if _SUMMARY_CACHE["data"] is not None and (now - _SUMMARY_CACHE["ts"] < _CACHE_TTL):
        if etag_header and etag_header == _SUMMARY_CACHE["etag"]:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED)
        return ORJSONResponse(content=_SUMMARY_CACHE["data"], headers={"ETag": _SUMMARY_CACHE["etag"], "Cache-Control": f"public, max-age={int(_CACHE_TTL)}"})

    avg_api_latency = parse_prometheus_metric("ttl_api_request_duration_seconds")
    avg_db_latency = parse_prometheus_metric("ttl_db_query_duration_seconds")
    avg_redis_latency = parse_prometheus_metric("ttl_redis_command_duration_seconds")

    summary = {
        "total_checks": int(parse_prometheus_metric("ttl_checks_total") or 0),
        "total_notifications_sent": int(parse_prometheus_metric("ttl_notifications_sent_total") or 0),
        "average_api_latency_seconds": avg_api_latency,
        "average_db_latency_seconds": avg_db_latency,
        "average_redis_latency_seconds": avg_redis_latency,
        "workers_online": int(parse_prometheus_metric("ttl_workers_online") or 0),
        "queue_depth": int(parse_prometheus_metric("ttl_queue_size") or 0),
        "health": {
            "api_latency": get_latency_health(avg_api_latency, yellow_threshold=0.5, red_threshold=1.0),
            "db_latency": get_latency_health(avg_db_latency, yellow_threshold=0.1, red_threshold=0.5),
            "redis_latency": get_latency_health(avg_redis_latency, yellow_threshold=0.01, red_threshold=0.1),
        }
    }

    etag = f'W/"{hashlib.sha256(str(summary).encode()).hexdigest()}"'
    _SUMMARY_CACHE["ts"] = now
    _SUMMARY_CACHE["etag"] = etag
    _SUMMARY_CACHE["data"] = summary

    if etag_header and etag_header == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    return ORJSONResponse(content=summary, headers={"ETag": etag, "Cache-Control": f"public, max-age={int(_CACHE_TTL)}"})
