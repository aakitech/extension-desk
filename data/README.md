# Data

Two pilot districts in Zimbabwe: **Chiredzi** (Masvingo) and **Mangwe** (Matabeleland South). Both are semi-arid and maize/sorghum-growing. They were first chosen because both have some of the most recent staple prices in WFP's Zimbabwe dataset; most price rows now come from FEWS NET (see below). The districts are defined in `src/extension_desk/districts.py`.

Every row has a `record_id` that tools return and advisories cite (prefixes `rain:`, `price:`, `hh:`, `plot:`, `pest:`), and a `source` column.

| File | Source | Covers | Doesn't cover |
|---|---|---|---|
| `rainfall.csv` | [CHIRPS v3.0](https://www.chc.ucsb.edu/data/chirps3) dekadal rainfall from the Climate Hazards Center, UC Santa Barbara (`scripts/fetch_rainfall.py`) | Dekadal (10-day) rainfall from Jul 2023 to the latest published dekad, plus the 1991-2020 normal for each dekad (the mean of the 30 final CHIRPS values; `normal_years` says how many), the departure in mm, and the anomaly %. Dekads up to Aug 2026 are **final** CHIRPS (Africa archive). Sep 2026 is **preliminary** CHIRPS (global archive), because CHC publishes final CHIRPS around the third week of the following month. `source` says which, and a refresh replaces prelim with final once it is published | One 0.05° CHIRPS pixel per district, the pixel containing the WFP market location (Ingwizi for Mangwe), not a district average. Chiredzi's point (-21.05) lies exactly on a pixel edge; the script takes the pixel to the south (row 1221 in the Africa grid). GDAL and rasterio, working from the file's float32 pixel size, put it in the pixel to the north (row 1220), so a re-check with those tools reads a neighbouring pixel. Preliminary CHIRPS uses fewer stations than final and can differ noticeably from it. Anomaly % is blank when the normal is under 5 mm (dry season), where a percentage is meaningless; use `departure_mm` there |
| `prices.csv` | [WFP food prices for Zimbabwe](https://data.humdata.org/dataset/wfp-food-prices-for-zimbabwe) via HDX, and the [FEWS NET Data Warehouse](https://fdw.fews.net/) public price API (`scripts/fetch_prices.py`) | Monthly retail prices from Dec 2023. **WFP** (61 rows, to May 2026): maize meal in all WFP markets and some maize grain in Chiredzi Urban. **FEWS NET** (247 rows, to Sep 2026): sorghum, pearl millet, shelled groundnuts, cowpeas and white maize grain at Rutenga (for Chiredzi), Gwanda and Plumtree (for Mangwe). All in USD per kg; FEWS NET rows are FEWS NET's own USD/kg conversion, with the quoted price, pack size and exchange rate kept in `source` | **Proxy markets.** FEWS NET has no market in Chiredzi; Rutenga is in neighbouring Mwenezi. Plumtree is Mangwe's district town but its series stops at Jan 2025; Gwanda is in the neighbouring district. Rutenga and Gwanda quote in rand, so their USD prices move with the exchange rate. Cowpeas at Rutenga only start in May 2026. FEWS NET has no data at any of these markets for Oct-Nov 2024 or Feb-Dec 2025 (marked "No Data" at source), and other months are missing too, and WFP's Brunaperg series stops at Dec 2024. Retail prices, not farm-gate prices |
| `households.csv` | **Synthetic** (`scripts/generate_synthetic.py`, fixed seed) | 25 households per district: ward, size, phone access, last officer visit | Real people. No names or contact details exist anywhere |
| `plots.csv` | **Synthetic** | 1 to 3 plots per household: crop, maturity, area, soil, irrigation, planting date | Real farms |
| `pest_alerts.csv` | **Synthetic** | 3 recent alerts per district, only for early-season pests of crops actually planted in that district, reported at least a week after the first planting | Real surveillance reports |

No real household records are used anywhere in this repo.

## The season assumption

The synthetic plots assume **no planting rains yet** for 2026/27 as of 12 Oct 2026, so only irrigated plots are planted. That is an assumption, not a sourced fact. The rainfall data partly cuts against it: Mangwe recorded 16.0 mm in the last dekad of September against a 2.9 mm normal (`rain:mangwe:2026-09-d3`, preliminary CHIRPS). Whether that is a false start is a real advisory question, and the agent should treat it as one rather than assume.

## Rainfall routes checked (12 Oct 2026)

- **CHIRPS v3.0 TIFFs over HTTP range requests**: used. The final Africa dekads and preliminary global dekads are LZW-compressed with one row per strip, so each dekad costs a few small requests rather than a 4 MB download. The decoder was checked against CHC's own BIL files for the same dekad: all 2,366 land pixels in four sample rows matched.
- **CHIRPS v2.0**: final Africa dekads are only published gzipped (about 4 MB each, so about 4.5 GB for the normals), the BILs are rounded down to whole millimetres, and the yearly NetCDFs are about 477 MB each. Not used.
- **ClimateSERV API** (server-side CHIRPS query): returns data, but the values didn't match CHC's dekadal files at the same pixel (Chiredzi, Aug 2026 dekad 3: 10.5 mm from ClimateSERV, against 4.6 mm in CHIRPS v2.0 final and 5.2 mm in v3.0 final), and the API doesn't say which product version it serves. Not used.
- **IRI Data Library**: the CHIRPS dataset now redirects to a login. Not used.
- **TAMSAT**: not checked, since CHIRPS covered everything needed.

## Price sources checked (12 Oct 2026)

- **WFP via HDX**: used for maize. Has no USD sorghum, groundnut or cowpea prices for any Zimbabwe market since 2025.
- **FEWS NET Data Warehouse** (`fdw.fews.net/api/marketpricefacts/`): used for everything else. Open API, no key, `data_usage_policy: Public` on every row.
- **FAO GIEWS FPMA** (`fpma.fao.org/giews/v4/global/price_module/api/v1/FpmaSerieDomestic/?iso3_country_code=ZWE`): 9 Zimbabwe series, all in ZiG, covering maize meal, beans, rice, vegetable oil and bread in Epworth (Harare), Bulawayo, Gweru, Mutare and Masvingo. No sorghum, millet, groundnuts or cowpeas, and no market in either pilot district. Not used.
- **GMB**: publishes one national producer price per crop per season, not a monthly market series, and no 2026 sorghum price was found. Not used.

## Refreshing

```bash
uv run python scripts/fetch_rainfall.py
uv run python scripts/fetch_prices.py
uv run python scripts/generate_synthetic.py
```

The committed CSVs are what the agent and the evals use, so a run never depends on the network.

## Attribution

- Rainfall: CHIRPS v3.0, Climate Hazards Center, UC Santa Barbara. The [CHIRPS3 page](https://www.chc.ucsb.edu/data/chirps3) states that it is in the public domain and licensed under CC BY 4.0.
- Food prices: World Food Programme, via the Humanitarian Data Exchange, CC BY-IGO; and Famine Early Warning Systems Network (FEWS NET) Data Warehouse, public data.
