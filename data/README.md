# Data

Two pilot districts in Zimbabwe: **Chiredzi** (Masvingo) and **Mangwe** (Matabeleland South). Both are semi-arid and maize/sorghum-growing, and both have some of the most recent staple prices in WFP's Zimbabwe dataset. The districts are defined in `src/extension_desk/districts.py`.

Every row has a `record_id` that tools return and advisories cite (prefixes `rain:`, `price:`, `hh:`, `plot:`, `pest:`), and a `source` column.

| File | Source | Covers | Doesn't cover |
|---|---|---|---|
| `rainfall.csv` | [Open-Meteo archive API](https://open-meteo.com/en/docs/historical-weather-api), ERA5 reanalysis (`scripts/fetch_rainfall.py`) | Dekadal (10-day) rainfall from Jul 2023 to the last complete dekad before the fetch, plus the 1991-2020 normal for each dekad (built from complete dekads only; `normal_years` says how many), the departure in mm, and the anomaly % | One ERA5 grid cell (0.25°) per district, at the WFP market location (Ingwizi for Mangwe), not a district average. ERA5 is a reanalysis model, not rain-gauge or satellite data like CHIRPS. Anomaly % is blank when the normal is under 5 mm (dry season), where a percentage is meaningless; use `departure_mm` there |
| `prices.csv` | [WFP food prices for Zimbabwe](https://data.humdata.org/dataset/wfp-food-prices-for-zimbabwe) via HDX (`scripts/fetch_prices.py`) | Monthly USD retail prices from Dec 2023 (when the USD series for these markets starts) to May 2026: **maize meal** in all markets, and some **maize grain** in Chiredzi | **Maize only.** WFP has no USD sorghum, groundnut or cowpea prices for any Zimbabwe market since 2025. Months are missing, and Brunaperg stops at Dec 2024. Chiredzi prices come from the Chiredzi Urban market, used as a proxy for the rural district. Retail prices, not farm-gate prices |
| `households.csv` | **Synthetic** (`scripts/generate_synthetic.py`, fixed seed) | 25 households per district: ward, size, phone access, last officer visit | Real people. No names or contact details exist anywhere |
| `plots.csv` | **Synthetic** | 1 to 3 plots per household: crop, maturity, area, soil, irrigation, planting date | Real farms |
| `pest_alerts.csv` | **Synthetic** | 3 recent alerts per district, only for early-season pests of crops actually planted in that district, reported at least a week after the first planting | Real surveillance reports |

No real household records are used anywhere in this repo.

## The season assumption

The synthetic plots assume **no planting rains yet** for 2026/27 as of 12 Oct 2026, so only irrigated plots are planted. That is an assumption, not a sourced fact. The rainfall data partly cuts against it: Mangwe recorded 21.4 mm in the last dekad of September against a 2.6 mm normal. Whether that is a false start is a real advisory question, and the agent should treat it as one rather than assume.

## Refreshing

```bash
uv run python scripts/fetch_rainfall.py
uv run python scripts/fetch_prices.py
uv run python scripts/generate_synthetic.py
```

The committed CSVs are what the agent and the evals use, so a run never depends on the network.

## Attribution

- Weather data by [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0. Underlying ERA5 data: Copernicus Climate Change Service (C3S).
- Food prices: World Food Programme, via the Humanitarian Data Exchange, CC BY-IGO.
