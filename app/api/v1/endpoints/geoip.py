from fastapi import APIRouter, Request, Query
from fastapi.responses import ORJSONResponse
from app.services import geoip

router = APIRouter()

@router.get("", response_class=ORJSONResponse)
async def lookup(request: Request, ip: str | None = Query(None)):
    """
    Anonymous GeoIP lookup.
    - If 'ip' is provided, look it up.
    - Otherwise, derive from X-Forwarded-For / X-Real-IP / client socket.
    """
    client_ip = ip
    if not client_ip and request:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            client_ip = xff.split(",")[0].strip()
        if not client_ip:
            client_ip = request.headers.get("x-real-ip")
        if not client_ip and request.client:
            client_ip = request.client.host

    details = geoip.get_geoip_details(client_ip or "")
    payload = {"ip": client_ip or "", **details}
    return ORJSONResponse(content=payload)
