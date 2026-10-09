# Locations: plan (milstolpe 1)

Underlag för granskning innan någon kod skrivs. Alla tabell-ID:n nedan är kontrollerade mot de riktiga API:erna den 9 oktober 2026 (metadata, senaste period, regionnivå och kodlistor). Baslinjen i repot: 80 tester (63 testfunktioner, några parametriserade) går igenom och `ruff check` är ren.

## 1. Vad koden har i dag (det som styr designen)

- `streamlit_app.py` bygger headerns söklista av bolag + län + städer *som har bolag*; `search()` är en ren funktion utan nivåer eller ranking mellan typer.
- Bolagens län och stad räknas fram i `ui/data._place` från `region_mix` (inte lagrat i snapshoten). `region_mix` ger hela fördelningen, så "Property companies here" kan visa bolagets andel i platsen, inte bara största region.
- Snapshots: Parquet per tabell, `TABLES`-DDL i `store/db.py`, `write_snapshot()` validerar via DuckDB. Platsdata är samma i live och demo, så den bör ligga i en egen katalog (`data/snapshots/locations/`) med egen `meta.json`, inte dupliceras i `live/` och `demo/`.
- `Fetcher` har robots, host-intervall, retry på 429/5xx och cache med TTL. SCB-klienten bygger på den.
- `streamlit_searchbox` renderas i en iframe. Det betyder att `theme.css` inte når förslagsraderna, och komponenten har ingen `formatOptionLabel`. Tvåfärgade rader ("Luthagen" stort, "Area · Uppsala municipality" i `--muted`) går inte att göra med den. Se beslut A nedan.

## 2. Verifierade källor

### SCB PxWebApi 2.0

`GET /config`: apiVersion 2.3.2, maxDataCells 150 000, 30 anrop per 10 s, CC0, json-stat2/csv/px/xlsx.

**Viktiga fynd vid verifieringen**

1. **Nollor utanför versionens giltighet.** TAB6574 returnerar `0`, inte `..`, för RegSO 2025-koder före 2024 och för RegSO 2020-koder efter 2023 (provat på Uppsala, Lindbacken-Jälla: 2020-koden har 204 → 3 107 invånare 2010–2023 och sedan 0, 0). Klienten måste maska på versionens giltighetsfönster (2020: t.o.m. 2023, 2025: fr.o.m. 2024), inte på värdet. TAB6586 använder `..` korrekt.
2. **RegSO 2020 → 2025.** SCB:s fil *Historiska förändringar i RegSO* (2026-03-25) visar att 288 av 3 363 koder i 99 kommuner fått ändrade gränser (delats, utvidgats, upphört). Övriga har samma kod och bara namnbyten eller utvidgning till territorialvatten. Plan: koppla ihop serierna för de 3 075 oförändrade koderna, markera tidsseriebrott för de 288. Filen konverteras en gång till `config/geo/regso_changes.csv` (med källa och datum) så att appen inte behöver ett xlsx-bibliotek.
3. **Ingen kommun- eller länsgeometri i WFS.** Lagren är RegSO, DeSO, tätorter, småorter, verksamhetsområden, handelsområden och 1 km-rutor. Kommun- och länsgränser tas fram genom att slå ihop RegSO 2025 (som följer kommungränserna).
4. **1 km-rutnät med befolkning 2025** (`stat:befolkning_1km_2025`, 115 118 rutor med ålder i 5-årsklasser). Ger ett mycket bättre befolkningsunderlag inom 50/100/200 km än RegSO-centroider, och ger befolkningsviktade RegSO-centroider. Används bara i pipelinen; rutnätet committas inte.
5. **Verksamhetsområden 2020** (WFS, 3 743 områden med anställda, arbetsställen, största bransch och yta) ger ett direkt mått på klustrade industri- och logistiklägen. Senaste upplagan är 2020.
6. **SNI 46 (partihandel) finns inte på kommunnivå** i någon aktuell tabell. På kommunnivå finns H (transport och magasinering), F (bygg) och B+C (tillverkning ihop med gruvor).

**Valda tabeller (ändringar mot din lista i fetstil)**

| Tabell | Innehåll | Nivå | Period | Används till |
|---|---|---|---|---|
| **TAB6574** | Folkmängd efter ålder (5-årsklasser) och kön | riket, län, kommun, RegSO 2020+2025, DeSO | 2010–2025 | Tillväxt 1/5/10 år, andel 20–34, RegSO-serier. **Ersätter TAB7046**, som bara har 10-årsklasser och därför inte ger 20–34 |
| TAB6638 | Lägenheter efter upplåtelseform | DeSO/RegSO 2025 | 2024–2025 | Andel hyresrätt/bostadsrätt per RegSO |
| TAB6586 | Socioekonomiskt index och områdestyp | RegSO 2020 (2011–23), 2025 (2024) | 2011–2024 | Riktning och nivå per RegSO |
| TAB6683 | Nettoinkomst, fasta priser | riket→RegSO | 2011–2024 | Betalningsförmåga |
| TAB6685 | Låg/hög ekonomisk standard | riket→RegSO | 2011–2024 | Risk, hyresutrymme |
| TAB6680 / TAB6681 | Arbetsmarknadsstatus; sysselsatta efter SNI (bostad) | DeSO/RegSO | 2020–2024 | Sysselsättningsgrad per RegSO |
| **TAB6534** | Utbildningsnivå 25–65 år | riket→RegSO 2025 | 2024–2025 | Kunskapsstad. **Före TAB7025** (20–74 år, uppdelad på bakgrund) |
| TAB6540 / TAB6621 | Bebyggelsens ålder; byggnader och markyta | riket→RegSO 2025 | 2010–2025 | Bestånd, markyta för industri |
| TAB6568 | Hushåll efter typ | riket→RegSO | 2011–2025 | Hushållsökning (bostadstryck) |
| TAB6589 | Personbilar | riket→RegSO 2025 | 2024–2025 | Bilar per invånare (visas, poängsätts inte) |
| TAB5724 / TAB5744 / TAB5750 | IntGr: flyttnetto, boende, inkomst | RegSO 2020 | 2007–2023 | Långa trender, versionsbrott 2024 |
| TAB6640 + TAB1212 | Flyttningar efter ålder | riket, län, kommun | 2025; 1997–2024 | Inrikes och utrikes netto, 20–34 år |
| TAB4572 | Påbörjade/färdigställda, kvartal | riket, län, kommun | 1975K1–2026K2 | Pipeline, bostadstryck |
| TAB4193 | Färdigställda efter upplåtelseform | riket, län, kommun | 1991–2025 | Hyresrättsutbud |
| TAB824 | Bestånd efter hustyp och upplåtelseform | riket, län, kommun | 1990–2025 | Bestånd |
| TAB4603 | Hyra efter antal rum | **20 större kommuner + Stockholms stadsområden** | 2016–2026 | Hyresnivå där den finns, annars – |
| TAB6259 | Årshyra per kvm | **bara riket** | 1969–2026 | Rikets hyresutveckling som referens |
| TAB6417 | Hyror i nybyggda hus efter hyressättningsmodell | **riket, Stor-Sthlm/Gbg/Malmö, övriga** | 2022–2025 | Presumtionshyrans premie, på storstadsnivå |
| TAB3656 | Småhusbarometern | riket, storstäder, län | 1997M04–2026M09 | Prisproxy (K/T) |
| TAB1156 | Försålda hyreshus | län, riket | 1989–2025 | K/T hyreshus |
| TAB1158 | Försålda industrier, inkl. **lagerbyggnad (typkod 432)** | län, riket | 1994–2025 | K/T industri och lager |
| TAB3797 | Fastighetstaxering efter typkod | riket, län, kommun | 1998–2025 | Taxeringsvärde **per taxeringsenhet** (tabellen har ingen yta, så "per kvm" går inte) |
| **TAB3204 + TAB3785** | Sysselsatta efter arbetsställets belägenhet och SNI | riket, län, kommun | 2020–2024 (register), 2025 (preliminär) | Dagbefolkning H, F, B+C. **Ersätter TAB5458**, som bara finns för län och FA-regioner |
| TAB1830 | Pendling bostadskommun × arbetsställekommun | kommun | 2020–2024 | Pendlingsnetto |
| TAB3143 | BRP per kommun | riket, kommun | 2012–2024 | BRP-tillväxt |
| TAB3554 | Sammanräknad förvärvsinkomst (median) | riket, län, kommun | 1999–2024 | Medianinkomst i faktaraden |
| TAB4947 | Bostäder nära kollektivtrafik | riket, län, kommun (inte RegSO) | 2014–2024 | Läge |
| TAB694 | Befolkningsframskrivning | län, kommun | 2024–2070 | Visas som referens |
| **TAB4060** | Folkmängd per tätort (årlig) | tätort | 2005–2025 | Tätortsnamn och folkmängd för sök. Tätortskoden (`0380TB108`) börjar med kommunkoden |
| **TAB3214** | Outhyrda lägenheter | **bara riket** | 2003–2024 | Ingen kommunserie finns (inte heller i Kolada). Bara riksvärdet som kontext; BME-överskott används som vakansmått |

Kodlistor från samma API (inga extra källor behövs):
- `agg_RegionKommungrupp2023-`: SKR:s kommungrupper 2023 (9 grupper) för filtret i Ranking.
- `agg_RegionLA2018`: lokala arbetsmarknader 2018 (69 LA) för "närmaste grannar". Tillväxtverkets FA-regioner finns bara som regionkoder utan medlemslista, så LA används.

### SCB WFS (`geodata.scb.se/geoserver/stat/wfs`)

GeoJSON i EPSG:4326 direkt från WFS fungerar (kontrollerat). För avståndsberäkningar hämtas samma lager i EPSG:3006 (SWEREF 99 TM, meter), så att inget pyproj behövs.

| Lager | Antal | Används till |
|---|---|---|
| `stat:RegSO_2025` | 3 363 | Kartor (förenklade, per kommun), kommun- och länsgränser genom sammanslagning |
| `stat:RegSO_2020` | 3 363 | Bara för att placera 2020-serier rätt |
| `stat:Tatorter_2023` | 2 017 | Vilka RegSO som ligger i en tätort |
| `stat:befolkning_1km_2025` | 115 118 | Befolkning inom 50/100/200 km, befolkningsviktade centroider |
| `stat:Verksamhetsomraden_2020` | 3 743 | Klustrade industri- och logistiklägen |

### Kolada v3 (`api.kolada.se/v3`)

- `U30446` bostadsmarknadsläge, `U30457` studenter, `U30460` ungdomar: kommun, 2013–2026. **Kodning: underskott = 0, balans = 1, överskott = 2.** Nollan är ett riktigt värde och får aldrig behandlas som saknat.
- `/municipality_groups`: 290 grupper "Liknande kommuner socioekonomi, <kommun>, 2024", 7 kommuner var (Uppsala: Linköping, Jönköping, Växjö, Lund, Göteborg, Örebro, Umeå).
- `N03937` öppet arbetslösa 18–65 år (till 2026), `N00904` skattekraft i % av riket (till 2027), `N01982` eftergymnasial utbildning 25–64 år.

### Övriga källor

| Källa | Beslut |
|---|---|
| Polisen, *Lägesbild över utsatta områden 2025* (PDF, dec 2025) | Hämtad och läst. **Kategorin riskområde är borttagen 2025**; 65 områden i två kategorier (utsatt, särskilt utsatt). Läggs manuellt i `config/geo/utsatta_omraden.yaml` med koppling till de RegSO som området överlappar. Polisens gränser följer inte RegSO, så kopplingen är ungefärlig och märks så. |
| Logistiklägen | **Intelligent Logistik lades ned 2024.** Dagens Logistik tog över 2025 och gör nu en nordisk lista med regioner, inte poäng per läge. Senaste upplagan (25 feb 2026): 1 Göteborgsregionen, 2 Öresundsregionen, 3 Mälardalsregionen, 7 Jönköpingsregionen, 8 Bottenviken. Sista svenska listan (IL 2024): Göteborg och Helsingborg delad etta; resten av tabellen finns bara som bild. Läggs i YAML med år och källa. |
| Svensk Mäklarstatistik | **Hoppas över.** Villkoren ger en icke-överlåtbar licens och förbjuder att ge tredje man tillgång till informationen. Beslutet skrivs i README. |
| Boverket, Arbetsförmedlingen | Behövs inte i första versionen: Kolada har BME och arbetslöshet. |
| Logistiknoder | `config/geo/logistics_nodes.yaml` för hamnar, kombiterminaler och fraktflygplatser, med koordinater och källa. Trafikplatser på E4/E6/E18/E20 hämtas en gång från OpenStreetMap (Overpass, `highway=motorway_junction`) med ett skript i `scripts/`, och resultatet committas som CSV. Trafikverkets API kräver nyckel och behövs inte. |
| Marknadsyields och hyror (Newsec, C&W, CBRE) | Ingår inte i första versionen. Strukturen för en manuell YAML (källa, datum, sida) förbereds. |

## 3. Datamodell

Ny snapshotkatalog `data/snapshots/locations/` (samma i live och demo), skriven med `write_snapshot` och nya DDL:er i `TABLES`-mönstret:

```
location            level, code, name, display_name, parent_kommun, parent_lan,
                    regso_version, tatort_codes, centroid_lat, centroid_lon,
                    population, population_year, kommungrupp, la_region
location_indicator  code, level, indicator, period, value, unit, source,
                    source_table, source_url, as_of, note
location_score      code, level, score_kind (residential|logistics), score,
                    coverage, components_json, as_of
location_peer       code, level, peer_set (kolada|nearest|twin), peer_code,
                    rank, distance, reason
meta.json           as_of per källtabell, tabellens "updated", regso-version
```

- `level`: `riket`, `lan`, `kommun`, `tatort`, `regso`.
- `period` som text (`2025`, `2026K2`, `2025M09`) så att år, kvartal och månad ryms i samma kolumn.
- `note` bär "derived", "series break 2024", "suppressed" osv. Ett undertryckt värde lagras som null med note, aldrig som 0.
- Geometri: `data/geo/sweden_kommun.geojson` och `data/geo/sweden_lan.geojson` (förenklade), samt `data/geo/regso/<kommunkod>.geojson`. Uppskattat 3–5 MB totalt; kartan laddar bara en kommuns fil åt gången.
- Uppskattad storlek på `location_indicator`: några hundra tusen rader, 3–6 MB som Parquet. RegSO får långa serier bara för befolkning, SEI och inkomst.

Kod:

```
src/headroom/sources/scb.py       PxWebApi-klient: /config, metadata, chunkade POST,
                                  rate limit (30/10 s), backoff på 429, cache per tabell,
                                  hoppar över när "updated" inte ändrats, json-stat2 → Polars
                                  (långt), '..' och '.' → null, versionsmask för RegSO
src/headroom/sources/kolada.py    v3-klient med paginering (next_url)
src/headroom/sources/scb_geo.py   WFS med paginering, förenkling, sammanslagning, centroider
src/headroom/model/location.py    indikatorer, percentiler, poäng, peers, rubrikregler,
                                  platsindex för sök (cachat)
src/headroom/pipeline.py          build_locations() → `uv run headroom locations`
app/views/locations.py            startläge och valt läge
config/geo/                       regso_changes.csv, utsatta_omraden.yaml,
                                  logistics_nodes.yaml, motorway_junctions.csv,
                                  logistics_ranking.yaml
```

## 4. Indikatorer

Varje indikator har värde, period, källtabell och käll-URL. Jämförelse: percentil i riket (bara när minst 20 jämförbara värden finns), länets värde, riksvärdet och medel för Kolada-gruppen.

**Bostäder (kommun och län)**
- Befolkningstillväxt 1, 5 och 10 år (TAB6574)
- Inrikes och utrikes flyttnetto per 1 000 invånare, samt inrikes netto 20–34 år (TAB6640, TAB1212), 3-årssnitt
- Bostadstryck = befolkningsökning per färdigställd lägenhet, 3-årssnitt (TAB6574, TAB4572). Hushållsökning per lägenhet visas bredvid (TAB6568)
- Påbörjade lägenheter per 1 000 invånare, senaste fyra kvartal (TAB4572)
- BME totalt, studenter, ungdomar och trend (Kolada)
- Andel hyresrätt i bestånd och i färdigställda (TAB824, TAB4193)
- Hyresnivå och förändring där TAB4603 täcker kommunen; presumtionspremie på storstadsnivå (TAB6417)
- Andel bostäder nära kollektivtrafik (TAB4947)
- Medianinkomst och nettoinkomst (TAB3554, TAB6683)

**Bostäder (RegSO)**: folkmängd och tillväxt, andel 20–34, andel hyresrätt, nettoinkomst och ekonomisk standard, SEI och områdestyp med 10-årstrend ("förbättras från låg nivå" som egen signal), utbildning, flagga för utsatt område.

**Logistik och lätt industri (kommun och län)**
- Sysselsatta på arbetsplatsen inom H, F och B+C, nivå, andel och tillväxt 2020–2025 (TAB3204, TAB3785)
- Befolkning inom 50, 100 och 200 km fågelvägen (1 km-rutor). I UI: "straight-line distance, an approximation of drive time"
- Avstånd till närmaste hamn, kombiterminal, fraktflygplats och trafikplats på E4/E6/E18/E20
- Pendlingsnetto (TAB1830), BRP-tillväxt (TAB3143)
- Anställda i verksamhetsområden per invånare, och markyta för industribyggnader (WFS, TAB6621)
- K/T för industri och lager (län, TAB1158), taxeringsvärde per industrienhet (TAB3797)
- Logistikrankingen (visas, poängsätts inte)

**Status och ekonomi**: öppen arbetslöshet, sysselsättningsgrad, utbildningsnivå, skattekraft.

## 5. Poängförslag (`config/weights.yaml`, nytt block `location:`)

Samma mönster som `motivated_seller`: linjär ramp mellan `zero` och `full`, vikter som summerar till 1,0, saknade komponenter tas bort och resten viktas om, ingen poäng under 50 % täckning. Rör inte `motivated_seller`, `strategy_fit` eller `scoring.py`.

```yaml
location:
  min_coverage: 0.50
  residential_kommun:
    weights:
      growth_5y: 0.20            # årlig befolkningstillväxt, 5 år
      housing_pressure: 0.20     # befolkningsökning per färdigställd lgh, 3 år
      young_inflow: 0.15         # inrikes netto 20–34 år per 1 000 inv., 3 år
      bme: 0.15                  # Kolada U30446, senaste år
      pipeline: 0.10             # påbörjade per 1 000 inv., 4 kvartal (färre = bättre)
      income: 0.10               # medianinkomst / riket
      education: 0.10            # andel 25–64 med eftergymnasial ≥ 3 år
    growth_5y:        {zero: 0.000, full: 0.015}
    housing_pressure: {zero: 1.0,   full: 2.5}
    young_inflow:     {zero: -2.0,  full: 6.0}
    bme:              {zero: 2,     full: 0}     # överskott → 0, underskott → 100
    pipeline:         {zero: 10.0,  full: 3.0}
    income:           {zero: 0.85,  full: 1.15}
    education:        {zero: 0.15,  full: 0.35}
  residential_regso:
    weights:
      growth: 0.25               # 5 år där serien är obruten, annars 1 år (märks)
      share_20_34: 0.15
      rental_share: 0.15
      sei_trend: 0.20            # förbättring av SEI/områdestyp över 10 år
      income_vs_kommun: 0.15
      kommun_context: 0.10       # kommunens residential score
    ...
  logistics_kommun:
    weights:
      catchment_100km: 0.25      # log-skala, 0,3 → 3 miljoner
      logistics_lq: 0.20         # andel H av sysselsatta / riket
      logistics_growth: 0.10
      node_distance: 0.15        # närmaste hamn/kombiterminal/fraktflyg, 80 → 10 km
      motorway_distance: 0.10    # 40 → 3 km
      job_hub: 0.10              # in-/utpendling, 0,7 → 1,3
      industrial_cluster: 0.10   # anställda i verksamhetsområden per invånare
  bands: {high: 65, watch: 45}
```

Län: samma formler på länsnivå där data finns; avståndsmåtten som befolkningsviktat snitt av kommunerna. Method-sidan får ett nytt avsnitt "Location scores" som läser blocket direkt.

## 6. Jämförbara platser

- **Liknande kommuner**: Kolada-gruppen (7 kommuner).
- **Närmaste grannar**: de 5 närmaste efter befolkningsviktad centroid, i första hand inom samma LA 2018, fyllt på utanför om LA har färre.
- **Statistiska tvillingar**: k-NN (k = 5, numpy) på standardiserade värden för log folkmängd, tillväxt 5 år, medianinkomst, andel hyresrätt och andel H-sysselsatta.
- Alltid med: länet och riket. För RegSO: övriga RegSO i kommunen, kommunens värde och riket.

## 7. Sidans två lägen (skiss)

Siffror i skisserna är platshållare (N, X), utom Uppsalas folkmängd 2025 och ökningen 2024–2025, som är hämtade ur TAB6574 vid verifieringen.

```
STARTLÄGE
┌──────────────────────────────────────────────┬──────────────────────┐
│ LOCATIONS                                    │ N    municipalities  │
│ N of 290 municipalities grew faster than     │ ──────────────────── │
│ Sweden and built less than they grew.        │ X.X  persons per new │
│                                              │      home, nationally│
│                                              │ N    areas improving │
├──────────────────────────────────────────────┴──────────────────────┤
│ Search a county, municipality, city or area________________         │  ← 2/3 bredd, 60 px, 21 px text
│   Luthagen              Area · Uppsala municipality                 │
│   Uppsala               Municipality · Uppsala län                  │
├─────────────────────────────────────────────────────────────────────┤
│ Ranking                                                             │
│ [County ▾] [Municipality group ▾] [Min. population ▾]               │
│  #  Resid. Logist.  Municipality         Growth 5y  Pressure  BME   │
│  1   NN     NN      Uppsala · Uppsala län  +X.X%/yr   X.X     Under │
│ [Export CSV] [Export Excel]                                         │
├─────────────────────────────────────────────────────────────────────┤
│ Map  (Residential | Logistics)   choropleth på kommunnivå, blå ramp │
└─────────────────────────────────────────────────────────────────────┘

VALT LÄGE  ?level=kommun&code=0380
┌──────────────────────────────────────────────┬──────────────────────┐
│ MUNICIPALITY · UPPSALA LÄN                   │ NN /100 Residential  │
│ Uppsala added 1,710 residents last year,     │ NN /100 Logistics    │
│ N times the national pace.                   │ 249,726 residents    │
├──────────┬──────────────┬──────────┬─────────┴──────────────────────┤
│ Population│ Growth 5y   │ Housing   │ Median income                 │
│ 249,726   │ +X.X%/yr    │ Shortage  │ SEK XXX k                     │
│ 2025      │ 2020–2025   │ 2026      │ 2024                          │
├─────────────────────────────────────────────────────────────────────┤
│ Residential   index 2015=100 vs län och riket │ påbörjade/färdigst. │
│               mot befolkningsökning │ hyresnivå │ source_line        │
│ Logistics     sysselsatta H │ 50/100/200 km │ noder │ K/T industri   │
│ Areas         RegSO-tabell + karta, klick → ?level=regso&code=...    │
│ Peers         plats (markerad), Kolada, grannar, tvillingar, län,    │
│               riket på samma rader                                   │
│ Property companies here   bolag, andel av portfölj, score, länk;     │
│               "See in Companies"                                    │
└─────────────────────────────────────────────────────────────────────┘
```

Tätort (`?level=tatort&code=0380TB108`) visar kommunens analys med tätortens namn i rubriken, och Areas filtreras på de RegSO som ligger i tätorten.

## 8. Sök

- Ett platsindex (riket, 21 län, 290 kommuner, ~2 000 tätorter, 3 363 RegSO) byggs i `model/location.py` och cachas med `st.cache_data` på snapshotens `generated_at`.
- RegSO matchas både på hela namnet ("Uppsala (Luthagen)") och på namnet inom parentes ("Luthagen").
- `search()` får en valfri rangordning per typ (bakåtkompatibelt): bolag, län och kommuner först, sedan tätorter, sist RegSO; högst 8 förslag.
- Headern: en plats ger två förslag, "Uppsala · municipality" (Locations) och "Companies in Uppsala" (Companies, bara om det finns bolag där).

## 9. Arbetsordning och risker

Milstolparna följer din ordning (2–7). Risker:
- **Tid för första hämtningen**: ~60–80 SCB-anrop för kommun/län och ~150–250 för RegSO, med 30 anrop/10 s blir det några minuter. Efterföljande körningar hämtar bara tabeller vars `updated` ändrats.
- **Polisens områden mot RegSO**: ungefärlig koppling, märks i UI.
- **Prestanda i sök**: ~5 700 poster. Typo-matchningen i `search()` är ren Python; jag förberäknar de vikta strängarna och mäter. Blir den för långsam (över ~50 ms) begränsas typo-toleransen för RegSO.
