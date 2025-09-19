from fastapi import APIRouter, Request, Response, status
from fastapi.responses import ORJSONResponse
from prometheus_client.registry import REGISTRY
import time
import hashlib
from app.core.redis_pool import ephemeral_redis

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

def parse_histogram_avg(metric_name: str, label_filter=None):
    """Compute average (sum/count) for a histogram, optionally filtering by labels.
    label_filter can be a callable that receives sample.labels and returns True/False.
    """
    total_sum = 0.0
    total_count = 0.0
    for metric in REGISTRY.collect():
        if metric.name != metric_name or metric.type != 'histogram':
            continue
        for s in metric.samples:
            # Histogram exports include multiple series; we only care about _sum and _count
            if not (s.name.endswith('_sum') or s.name.endswith('_count')):
                continue
            labels = getattr(s, 'labels', {}) or {}
            if callable(label_filter) and not label_filter(labels):
                continue
            if s.name.endswith('_sum'):
                total_sum += s.value
            elif s.name.endswith('_count'):
                total_count += s.value
    if total_count > 0:
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
    # Average ping processing latency from dedicated histogram
    avg_ping_latency = parse_prometheus_metric("ttl_ping_process_time_seconds")
    if avg_ping_latency is None:
        # Fallback to overall API latency until ping endpoint is instrumented
        avg_ping_latency = avg_api_latency

    # Redis-backed global aggregates for multi-worker correctness
    total_checks_redis = None
    workers_online_redis = None
    queue_depth_redis = None
    avg_db_latency_redis = None
    avg_ping_latency_redis = None
    notifications_total_redis = None
    try:
        async with ephemeral_redis() as r:
            if r:
                # Total checks from global status hash
                counts = await r.hgetall("metrics:checks_status_counts") or {}
                def _to_int(v):
                    try:
                        if isinstance(v, (bytes, bytearray)):
                            v = v.decode()
                        return int(v)
                    except Exception:
                        return 0
                if counts:
                    total_checks_redis = sum(_to_int(v) for v in counts.values())

                # Workers online via heartbeats
                members = await r.smembers("metrics:workers_online:set")
                if members:
                    wo = 0
                    for m in members:
                        mid = m.decode() if isinstance(m, (bytes, bytearray)) else m
                        if await r.exists(f"metrics:worker:{mid}:hb"):
                            wo += 1
                        else:
                            await r.srem("metrics:workers_online:set", mid)
                    workers_online_redis = wo
                else:
                    workers_online_redis = 0

                # Queue depth directly from Redis
                q = await r.llen("rtt_celery_queue")
                if q is None or q == 0:
                    try:
                        q2 = await r.llen("celery")
                        queue_depth_redis = int(q2 or 0)
                    except Exception:
                        queue_depth_redis = int(q or 0)
                else:
                    queue_depth_redis = int(q or 0)

                # Averages from Redis sum/count
                try:
                    db_sum = await r.get("metrics:latency:db:sum")
                    db_count = await r.get("metrics:latency:db:count")
                    if db_sum and db_count:
                        db_sum_v = float(db_sum.decode() if isinstance(db_sum, (bytes, bytearray)) else db_sum)
                        db_count_v = float(db_count.decode() if isinstance(db_count, (bytes, bytearray)) else db_count)
                        if db_count_v > 0:
                            avg_db_latency_redis = db_sum_v / db_count_v
                except Exception:
                    pass
                try:
                    ping_sum = await r.get("metrics:latency:ping:sum")
                    ping_count = await r.get("metrics:latency:ping:count")
                    if ping_sum and ping_count:
                        ping_sum_v = float(ping_sum.decode() if isinstance(ping_sum, (bytes, bytearray)) else ping_sum)
                        ping_count_v = float(ping_count.decode() if isinstance(ping_count, (bytes, bytearray)) else ping_count)
                        if ping_count_v > 0:
                            avg_ping_latency_redis = ping_sum_v / ping_count_v
                except Exception:
                    pass

                # Notifications total (optional)
                try:
                    nt = await r.get("metrics:notifications_sent:total")
                    if nt:
                        notifications_total_redis = int(nt.decode() if isinstance(nt, (bytes, bytearray)) else nt)
                except Exception:
                    pass
    except Exception:
        pass

    if avg_db_latency_redis is not None:
        avg_db_latency = avg_db_latency_redis
    if avg_ping_latency_redis is not None:
        avg_ping_latency = avg_ping_latency_redis

    summary = {
        "total_checks": int(total_checks_redis if total_checks_redis is not None else (parse_prometheus_metric("ttl_checks_total") or 0)),
        "total_notifications_sent": int(notifications_total_redis if notifications_total_redis is not None else (parse_prometheus_metric("ttl_notifications_sent_total") or 0)),
        "average_api_latency_seconds": avg_api_latency,
        "average_db_latency_seconds": avg_db_latency,
        "average_redis_latency_seconds": avg_redis_latency,
        "average_ping_process_time_seconds": avg_ping_latency,
        "workers_online": int(workers_online_redis if workers_online_redis is not None else (parse_prometheus_metric("ttl_workers_online") or 0)),
        "queue_depth": int(queue_depth_redis if queue_depth_redis is not None else (parse_prometheus_metric("ttl_queue_size") or 0)),
        "health": {
            "api_latency": get_latency_health(avg_api_latency, yellow_threshold=0.5, red_threshold=1.0),
            "db_latency": get_latency_health(avg_db_latency, yellow_threshold=0.1, red_threshold=0.5),
            "redis_latency": get_latency_health(avg_redis_latency, yellow_threshold=0.01, red_threshold=0.1),
            "ping_latency": get_latency_health(avg_ping_latency, yellow_threshold=0.15, red_threshold=0.5),
        }
    }

    etag = f'W/"{hashlib.sha256(str(summary).encode()).hexdigest()}"'
    _SUMMARY_CACHE["ts"] = now
    _SUMMARY_CACHE["etag"] = etag
    _SUMMARY_CACHE["data"] = summary

    if etag_header and etag_header == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    return ORJSONResponse(content=summary, headers={"ETag": etag, "Cache-Control": f"public, max-age={int(_CACHE_TTL)}"})
