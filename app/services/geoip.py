import logging
import geoip2.database
from app.core.config import settings

logger = logging.getLogger(__name__)

_reader = None

def initialize_geoip():
    """Initializes the GeoIP database reader."""
    global _reader
    if not settings.GEOIP_DATABASE_PATH:
        logger.warning("GEOIP_DATABASE_PATH is not set. GeoIP lookups will be disabled.")
        return

    try:
        _reader = geoip2.database.Reader(settings.GEOIP_DATABASE_PATH)
        logger.info(f"Successfully loaded GeoIP database from: {settings.GEOIP_DATABASE_PATH}")
    except FileNotFoundError:
        logger.error(f"GeoIP database file not found at: {settings.GEOIP_DATABASE_PATH}. GeoIP lookups will be disabled.")
        _reader = None
    except Exception as e:
        logger.error(f"Failed to load GeoIP database: {e}")
        _reader = None

def get_geoip_details(ip_address: str) -> dict:
    """Looks up GeoIP details for a given IP address."""
    if not _reader or not ip_address:
        return {
            "country_code": "XX",
            "country_name": "Unknown",
            "connection_type": "Unknown",
        }

    try:
        # Use city database to get connection type
        response = _reader.city(ip_address)
        country_code = response.country.iso_code or "XX"
        country_name = response.country.name or "Unknown"
        
        # Determine connection type based on ISP/organization
        domain = response.traits.organization.lower() if response.traits.organization else ""
        if any(kw in domain for kw in ["vpn", "proxy", "tor"]):
            connection_type = "VPN/Proxy"
        elif any(kw in domain for kw in ["aws", "google", "azure", "oracle", "digitalocean", "linode", "hetzner", "ovh"]):
            connection_type = "Datacenter"
        elif response.traits.is_residential_proxy:
             connection_type = "Residential Proxy"
        else:
            connection_type = "Residential/Business"

        return {
            "country_code": country_code,
            "country_name": country_name,
            "connection_type": connection_type,
        }
    except geoip2.errors.AddressNotFoundError:
        return {
            "country_code": "XX",
            "country_name": "Unknown (Private IP)",
            "connection_type": "Unknown",
        }
    except Exception as e:
        logger.warning(f"GeoIP lookup failed for IP {ip_address}: {e}")
        return {
            "country_code": "XX",
            "country_name": "Error",
            "connection_type": "Error",
        }
