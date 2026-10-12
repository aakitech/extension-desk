# Data

Two pilot districts in Zimbabwe: **Chiredzi** (Masvingo) and **Mangwe** (Matabeleland South). Both are semi-arid and maize/sorghum-growing, and both have the most recent staple prices in WFP's Zimbabwe dataset. The districts are defined in `src/extension_desk/districts.py`.

Every row has a `record_id` that tools return and advisories cite, and a `source` column.

| File | Source | Covers | Doesn't cover |
|---|---|---|---|
| `rainfall.csv` | [Open-Meteo archive API](https://open-meteo.com/en/docs/historical-weather-api), ERA5 reanalysis (`scripts/fetch_rainfall.py`) | Dekadal (10-day) rainfall from Jul 2023 to about a week before the fetch date, plus the 1991-2020 normal for each dekad and the anomaly % | One point per district (the WFP market location), not a district average. ERA5 is a reanalysis model, not rain-gauge or satellite data like CHIRPS. Anomaly % is left blank when the normal is under 5 mm (dry season), where a percentage is meaningless |
| `prices.csv` | [WFP food prices for Zimbabwe](https://data.humdata.org/dataset/wfp-food-prices-for-zimbabwe) via HDX, CC BY-IGO (`scripts/fetch_prices.py`) | Monthly USD retail prices since 2020 for the district markets: mostly **maize meal**, with some **maize grain** in Chiredzi | Coverage is patchy: months are missing, and the latest data at fetch time was **May 2026**. No sorghum, groundnut or cowpea prices for these markets. Retail prices, not farm-gate prices |
| `households.csv` | **Synthetic** (`scripts/generate_synthetic.py`, fixed seed) | 25 households per district: ward, size, phone access, last officer visit | Real people. No names or contact details exist anywhere |
| `plots.csv` | **Synthetic** | 1 to 3 plots per household: crop, maturity, area, soil, irrigation, planting date. As of 12 Oct 2026 the rainy season hasn't started, so only irrigated plots are planted | Real farms |
| `pest_alerts.csv` | **Synthetic** | 3 recent alerts per district: pest, crops affected, severity | Real surveillance reports |

No real household records are used anywhere in this repo.

## Refreshing

```bash
uv run python scripts/fetch_rainfall.py
uv run python scripts/fetch_prices.py
uv run python scripts/generate_synthetic.py
```

The committed CSVs are what the agent and the evals use, so a run never depends on the network.
