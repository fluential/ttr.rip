import logging
from typing import Optional
import geoip2.database
from app.core.config import settings

logger = logging.getLogger(__name__)

_city_reader: Optional[geoip2.database.Reader] = None
_asn_reader: Optional[geoip2.database.Reader] = None

def initialize_geoip():
    """Initializes the GeoIP database readers (City and ASN)."""
    global _city_reader, _asn_reader

    # City DB
    if settings.GEOIP_DATABASE_PATH:
        try:
            _city_reader = geoip2.database.Reader(settings.GEOIP_DATABASE_PATH)
            logger.info(f"Successfully loaded GeoIP City database from: {settings.GEOIP_DATABASE_PATH}")
        except FileNotFoundError:
            logger.error(f"GeoIP City DB file not found at: {settings.GEOIP_DATABASE_PATH}. City lookups will be disabled.")
            _city_reader = None
        except Exception as e:
            logger.error(f"Failed to load GeoIP City database: {e}")
            _city_reader = None
    else:
        logger.warning("GEOIP_DATABASE_PATH is not set. GeoIP City lookups will be disabled.")

    # ASN DB (optional)
    if settings.GEOIP_ASN_DATABASE_PATH:
        try:
            _asn_reader = geoip2.database.Reader(settings.GEOIP_ASN_DATABASE_PATH)
            logger.info(f"Successfully loaded GeoIP ASN database from: {settings.GEOIP_ASN_DATABASE_PATH}")
        except FileNotFoundError:
            logger.error(f"GeoIP ASN DB file not found at: {settings.GEOIP_ASN_DATABASE_PATH}. ASN lookups will be disabled.")
            _asn_reader = None
        except Exception as e:
            logger.error(f"Failed to load GeoIP ASN database: {e}")
            _asn_reader = None
    else:
        _asn_reader = None  # silently optional

def _infer_connection_type(org: str | None, is_res_proxy: bool | None) -> str:
    org_l = (org or "").lower()
    if any(kw in org_l for kw in ["vpn", "proxy", "tor"]):
        return "VPN/Proxy"
    if any(kw in org_l for kw in ["aws", "google", "azure", "oracle", "digitalocean", "linode", "hetzner", "ovh", "cloudflare"]):
        return "Datacenter"
    if is_res_proxy:
        return "Residential Proxy"
    return "Residential/Business"

def get_geoip_details(ip_address: str) -> dict:
    """
    Looks up GeoIP details for a given IP address using the loaded City and ASN databases.
    Returns a dict with:
      country_code, country_name, region, region_code, city, latitude, longitude, timezone,
      asn, asn_org, connection_type
    """
    if not ip_address:
        return {
            "country_code": "XX",
            "country_name": "Unknown",
            "region": None,
            "region_code": None,
            "city": None,
            "latitude": None,
            "longitude": None,
            "timezone": None,
            "asn": None,
            "asn_org": None,
            "connection_type": "Unknown",
        }

    country_code = "XX"
    country_name = "Unknown"
    region = None
    region_code = None
    city = None
    latitude = None
    longitude = None
    timezone = None
    org_for_type = None
    is_res_proxy = None

    # City lookup
    try:
        if _city_reader:
            resp = _city_reader.city(ip_address)
            country_code = resp.country.iso_code or country_code
            country_name = resp.country.name or country_name
            if resp.subdivisions and len(resp.subdivisions) > 0:
                region = resp.subdivisions[0].name or None
                region_code = resp.subdivisions[0].iso_code or None
            city = resp.city.name or None
            if getattr(resp, "location", None):
                latitude = getattr(resp.location, "latitude", None)
                longitude = getattr(resp.location, "longitude", None)
                timezone = getattr(resp.location, "time_zone", None)
            # Traits for connection type hints
            try:
                org_for_type = getattr(resp.traits, "organization", None)
            except Exception:
                org_for_type = None
            try:
                is_res_proxy = getattr(resp.traits, "is_residential_proxy", None)
            except Exception:
                is_res_proxy = None
    except geoip2.errors.AddressNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"GeoIP City lookup failed for IP {ip_address}: {e}")

    # ASN lookup
    asn = None
    asn_org = None
    try:
        if _asn_reader:
            a = _asn_reader.asn(ip_address)
            asn = getattr(a.autonomous_system_number, "real", None) if hasattr(a.autonomous_system_number, "real") else a.autonomous_system_number
            asn_org = a.autonomous_system_organization or None
            # prefer ASN org for connection type
            if asn_org:
                org_for_type = asn_org
    except geoip2.errors.AddressNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"GeoIP ASN lookup failed for IP {ip_address}: {e}")

    connection_type = _infer_connection_type(org_for_type, is_res_proxy)

    return {
        "country_code": country_code or "XX",
        "country_name": country_name or "Unknown",
        "region": region,
        "region_code": region_code,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone,
        "asn": asn,
        "asn_org": asn_org,
        "connection_type": connection_type,
    }
