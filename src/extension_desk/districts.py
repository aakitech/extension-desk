"""Pilot districts. Coordinates are the WFP reference market for each district,
used as the point for rainfall lookups.

`markets` are WFP market names. `fews_markets` maps FEWS NET market names to
the short name used in price records; Rutenga (Mwenezi) and Gwanda are
neighbouring-district proxies, since FEWS NET has no market in Chiredzi and
Plumtree's series stops in Jan 2025."""

DISTRICTS = {
    "chiredzi": {
        "name": "Chiredzi",
        "province": "Masvingo",
        "lat": -21.05,
        "lon": 31.67,
        "markets": ["Chiredzi Urban"],
        "fews_markets": {"Mwenezi, Rutenga market (Open market)": "Rutenga"},
    },
    "mangwe": {
        "name": "Mangwe",
        "province": "Matabeleland South",
        "lat": -21.12,
        "lon": 27.87,
        "markets": ["Ingwizi", "Brunaperg", "Marula"],
        "fews_markets": {
            "Plumtree, Solwezi-A market (Open market)": "Plumtree",
            "Gwanda, Gwanda center (Open market)": "Gwanda",
        },
    },
}
