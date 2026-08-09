
import logging

import requests

from shared.constants.google_maps import GOOGLE_MAPS_API_KEY, GOOGLE_MAPS_DEFAULT_COUNTRY

logger = logging.getLogger(__name__)


class GoogleMapsService:
    """
    Wraps the Google Maps Geocoding API.
    
    Follows the same pattern as paystack_service.py — a plain class with
    @staticmethod methods so it can be called without instantiation.
    """

    GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    AUTOCOMPLETE_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"
    
    
    # >>>>>>>>>>>> Geocode Address  <<<<<<<<<<<<<<<<<<<

    @staticmethod
    def geocode_address(address: str) -> dict | None:
        """
        Convert a human-readable address into latitude and longitude.
        
        Returns None if geocoding fails or the address is not found.

        Usage:
            result = GoogleMapsService.geocode_address("Ikeja, Lagos")
            if result:
                lat = result["latitude"]
                lng = result["longitude"]
        """
        # Bias + restrict to the configured market country (NG by default) so a Lagos street name does not collide with a same-name street elsewhere. `region` is a soft bias; `components=country:<code>` is a hard filter. Configurable via the GOOGLE_MAPS_DEFAULT_COUNTRY env var so multi-country expansion needs no code changes.
        country = GOOGLE_MAPS_DEFAULT_COUNTRY.lower()
        params = {
            "address": address,
            "key": GOOGLE_MAPS_API_KEY,
            "region": country,
            "components": f"country:{country.upper()}",
        }

        try:
            response = requests.get(
                GoogleMapsService.GEOCODING_URL,
                params=params,
                timeout=5, 
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            # Network failure — fail silently and let the caller decide
            return None

        if data.get("status") != "OK" or not data.get("results"):
            return None

        result = data["results"][0]
        location = result["geometry"]["location"]

        return {
            "latitude": location["lat"],
            "longitude": location["lng"],
            "formatted_address": result.get("formatted_address", address),
        }
        
    
    # >>>>>>>>>>>> Places Autocomplete  <<<<<<<<<<<<<<<<<<<
    
    @staticmethod
    def places_autocomplete(input: str, session_token: str|None = None) -> list[dict]:
        """
        Returns address suggestions for the given partial input string.
        
        Returns an empty list on failure or no results.
        """
        
        # Hard-restrict predictions to the configured market country (NG by default). Without this Google returns globally-popular hits biased by the server's egress IP — that's why a Nigerian-targeted app was seeing India results in autocomplete. Configurable via the GOOGLE_MAPS_DEFAULT_COUNTRY env var; pipe-separate to allow more than one (e.g. "ng|gh").
        country = GOOGLE_MAPS_DEFAULT_COUNTRY.lower()
        components = "|".join(f"country:{c}" for c in country.split("|"))
        params = {
            "input": input,
            "key": GOOGLE_MAPS_API_KEY,
            "types": "geocode",
            "language": "en",
            "components": components,
        }
        
        if session_token:
            params["sessiontoken"] = session_token

        try:
            response = requests.get(
                GoogleMapsService.AUTOCOMPLETE_URL,
                params=params,
                timeout=5,
            )        
            response.raise_for_status()
            data = response.json()
            logger.debug("Google places autocomplete response: %s", data)

        except requests.RequestException:
            return []
        
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            return []
        
        return [
            {"description": p["description"], "place_id": p["place_id"]}
            for p in data.get("predictions", [])
        ]
        
    
    # >>>>>>>>>>>> Logistics distance calculator <<<<<<<<<<<<<<<<<<<
    
    DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

    @staticmethod
    def distance_km(
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> float | None:
        """
        Driving distance in kilometres between two coordinate pairs, via the Google Distance Matrix API.

        Returns the road distance (not straight-line) so delivery pricing reflects what the courier actually drives. Returns None on any failure (network, quota, no route found) — the caller decides how to handle a missing distance (we fail the quote's delivery calc rather than guess).

        Usage:
            km = GoogleMapsService.distance_km(6.45, 3.39, 6.43, 3.42)
            if km is not None:
                fare = base + km * rate
        """
        params = {
            "origins": f"{origin_lat},{origin_lng}",
            "destinations": f"{dest_lat},{dest_lng}",
            "key": GOOGLE_MAPS_API_KEY,
            "mode": "driving",
            # Metric => distance.value is in metres.
            "units": "metric",
        }

        try:
            response = requests.get(
                GoogleMapsService.DISTANCE_MATRIX_URL,
                params=params,
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            logger.warning("Distance Matrix request failed for %s,%s -> %s,%s",
                           origin_lat, origin_lng, dest_lat, dest_lng)
            return None

        if data.get("status") != "OK":
            logger.warning("Distance Matrix non-OK status: %s", data.get("status"))
            return None

        try:
            element = data["rows"][0]["elements"][0]
        except (IndexError, KeyError):
            return None

        if element.get("status") != "OK":
            # e.g. ZERO_RESULTS / NOT_FOUND — no drivable route between points.
            logger.info("Distance Matrix element status: %s", element.get("status"))
            return None

        meters = element.get("distance", {}).get("value")
        if meters is None:
            return None

        return round(meters / 1000.0, 2)
    
    @staticmethod
    def distance_and_duration(
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> tuple[float, int] | None:
        """
        Driving distance (km) AND duration (minutes) between two points, via the Distance Matrix API. Returns (km, minutes) or None on any failure.

        Used for the courier's per-stop ETA and the homeowner's delivery ETA. Same fail-soft contract as distance_km — None means 'couldn't compute', and the caller decides what to do (we don't guess an ETA).
        """
        params = {
            "origins": f"{origin_lat},{origin_lng}",
            "destinations": f"{dest_lat},{dest_lng}",
            "key": GOOGLE_MAPS_API_KEY,
            "mode": "driving",
            "units": "metric",
        }
        try:
            response = requests.get(
                GoogleMapsService.DISTANCE_MATRIX_URL, params=params, timeout=5,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            logger.warning("Distance+duration request failed for %s,%s -> %s,%s",
                           origin_lat, origin_lng, dest_lat, dest_lng)
            return None

        if data.get("status") != "OK":
            logger.warning("Distance+duration non-OK status: %s", data.get("status"))
            return None

        try:
            element = data["rows"][0]["elements"][0]
        except (IndexError, KeyError):
            return None

        if element.get("status") != "OK":
            logger.info("Distance+duration element status: %s", element.get("status"))
            return None

        meters = element.get("distance", {}).get("value")
        seconds = element.get("duration", {}).get("value")
        if meters is None or seconds is None:
            return None

        km = round(meters / 1000.0, 2)
        minutes = round(seconds / 60)
        return km, minutes