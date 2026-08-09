from decouple import config

GOOGLE_MAPS_API_KEY = config("GOOGLE_MAPS_API_KEY")

# ISO 3166-1 alpha-2 country code used to restrict Places Autocomplete and
# bias Geocoding. Set in env to override per-environment; defaults to "ng"
# because Feexeet is Nigeria-only today. Multi-country expansion changes
# this to a pipe-separated list (e.g. "ng|gh") in env without code changes.
GOOGLE_MAPS_DEFAULT_COUNTRY = config("GOOGLE_MAPS_DEFAULT_COUNTRY", default="ng")