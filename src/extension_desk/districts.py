"""Pilot districts. Coordinates are the WFP reference market for each district,
used as the point for rainfall lookups."""

DISTRICTS = {
    "chiredzi": {
        "name": "Chiredzi",
        "province": "Masvingo",
        "lat": -21.05,
        "lon": 31.67,
        "markets": ["Chiredzi Urban"],
    },
    "mangwe": {
        "name": "Mangwe",
        "province": "Matabeleland South",
        "lat": -21.12,
        "lon": 27.87,
        "markets": ["Ingwizi", "Brunaperg", "Marula"],
    },
}
