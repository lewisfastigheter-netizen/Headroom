"""Build the location snapshot: `uv run headroom locations`.

Writes data/snapshots/locations/ (the same public statistics in live and demo mode):

    location.parquet            one row per place (riket, län, kommun, tätort, RegSO)
    location_indicator.parquet  long format, one row per (place, indicator, period),
                                with unit, source, source table, source URL and as-of date
    location_score.parquet      Residential and Logistics scores (config/weights.yaml)
    location_peer.parquet       peer sets with the reason for each peer
    geo/                        simplified GeoJSON (Sweden by kommun and län, RegSO per kommun)
    meta.json                   as-of date and `updated` stamp per source table

Every value comes from a named table. SCB's dots ('..') are null, never zero; values a
table returns outside a RegSO version's years are null too (see sources/scb.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import yaml

from headroom.config import CONFIG_DIR
from headroom.http import Fetcher
from headroom.sources import kolada
from headroom.sources.scb import SCB, table_url
from headroom.store.db import snapshot_dir, write_snapshot

log = logging.getLogger(__name__)

GEO_DIR = CONFIG_DIR / "geo"
SCB_SOURCE = "SCB"
KOLADA_SOURCE = "Kolada (RKA)"
GEO_SOURCE = "SCB öppna geodata"

INDICATOR_COLS = {
    "code": pl.Utf8,
    "level": pl.Utf8,
    "indicator": pl.Utf8,
    "period": pl.Utf8,
    "value": pl.Float64,
    "unit": pl.Utf8,
    "source": pl.Utf8,
    "source_table": pl.Utf8,
    "source_url": pl.Utf8,
    "as_of": pl.Utf8,
    "note": pl.Utf8,
}


def level_of(code: str) -> str:
    if code == "00":
        return "riket"
    if len(code) == 2:
        return "lan"
    if len(code) == 4 and code.isdigit():
        return "storstad" if code.startswith("00") else "kommun"
    if len(code) == 8 and code[4] == "R":
        return "regso"
    if "T" in code[4:]:
        return "tatort"
    return "other"


def kommuner() -> pl.DataFrame:
    return pl.read_csv(
        GEO_DIR / "kommuner.csv", schema_overrides={"kommun_code": pl.Utf8, "county_code": pl.Utf8}
    )


def admin_codes() -> list[str]:
    k = kommuner()
    return ["00", *sorted(set(k["county_code"])), *k["kommun_code"].to_list()]


class Collector:
    """Accumulates indicator rows and the as-of stamp of each source table."""

    def __init__(self, scb: SCB):
        self.scb = scb
        self.frames: list[pl.DataFrame] = []
        self.as_of: dict[str, str] = {}

    def add(
        self,
        frame: pl.DataFrame,
        indicator: str,
        unit: str,
        table: str,
        *,
        source: str = SCB_SOURCE,
        url: str | None = None,
        note: str | None = None,
    ) -> None:
        """frame: columns code, period, value (+ optional note)."""
        if frame.is_empty():
            log.warning("%s: no rows for %s", table, indicator)
            return
        as_of = self.as_of.get(table, date.today().isoformat())
        f = frame.select(
            pl.col("code").cast(pl.Utf8),
            pl.col("period").cast(pl.Utf8),
            pl.col("value").cast(pl.Float64),
            (pl.col("note") if "note" in frame.columns else pl.lit(note))
            .cast(pl.Utf8)
            .alias("note"),
        ).with_columns(
            pl.col("code").map_elements(level_of, return_dtype=pl.Utf8).alias("level"),
            pl.lit(indicator).alias("indicator"),
            pl.lit(unit).alias("unit"),
            pl.lit(source).alias("source"),
            pl.lit(table).alias("source_table"),
            pl.lit(url or table_url(table)).alias("source_url"),
            pl.lit(as_of).alias("as_of"),
        )
        self.frames.append(f.select(list(INDICATOR_COLS)))

    def data(self, table: str, selection: dict, codelists: dict | None = None) -> pl.DataFrame:
        df = self.scb.data(table, selection, codelists)
        t = self.scb.metadata(table)
        self.as_of[table] = (t.updated or "")[:10]
        return df

    def frame(self) -> pl.DataFrame:
        if not self.frames:
            return pl.DataFrame(schema=INDICATOR_COLS)
        # A value beats a null for the same place, indicator and period (a table may list
        # a code it has no figure for while another table has one).
        return (
            pl.concat(self.frames, how="vertical_relaxed")
            .with_columns(pl.col("value").is_not_null().alias("_has"))
            .sort("_has", maintain_order=True)
            .unique(["code", "indicator", "period"], keep="last", maintain_order=True)
            .drop("_has")
        )


def _sum(df: pl.DataFrame, by: list[str] | None = None) -> pl.DataFrame:
    """Sum `value` over the other dimensions; null if every part is null."""
    by = by or ["region", "period"]
    return (
        df.group_by(by)
        .agg(
            pl.when(pl.col("value").is_not_null().any())
            .then(pl.col("value").sum())
            .otherwise(None)
            .alias("value")
        )
        .rename({"region": "code"})
    )


def _pick(df: pl.DataFrame, **eq: str) -> pl.DataFrame:
    for k, v in eq.items():
        df = df.filter(pl.col(k) == v)
    return df.rename({"region": "code"}) if "region" in df.columns else df


# --------------------------------------------------------------------------- SCB tables


def fetch_population(c: Collector) -> None:
    ages = ["totalt", "20-24", "25-29", "30-34"]
    admin = c.data(
        "TAB6574", {"Region": admin_codes(), "Alder": ages, "Kon": ["1+2"], "ContentsCode": "*"}
    )
    reg25 = c.data(
        "TAB6574",
        {
            "Region": "*",
            "Alder": ages,
            "Kon": ["1+2"],
            "ContentsCode": "*",
            "Tid": ["2024", "2025"],
        },
        {"Region": "vs_RegSO2025"},
    )
    years20 = [str(y) for y in range(2010, 2024)]
    reg20 = c.data(
        "TAB6574",
        {"Region": "*", "Alder": ages, "Kon": ["1+2"], "ContentsCode": "*", "Tid": years20},
        {"Region": "vs_RegSO2020"},
    )
    reg20 = reg20.with_columns(pl.lit("RegSO 2020 borders").alias("note"))
    for df in (admin, reg25, reg20):
        tot = _pick(df, Alder="totalt").select(
            "code", "period", "value", *(["note"] if "note" in df.columns else [])
        )
        c.add(tot, "pop", "persons", "TAB6574")
        young = df.filter(pl.col("Alder") != "totalt")
        ys = _sum(young, ["region", "period"])
        if "note" in df.columns:
            ys = ys.with_columns(pl.lit("RegSO 2020 borders").alias("note"))
        c.add(ys, "pop_20_34", "persons", "TAB6574")


def fetch_households(c: Collector) -> None:
    df = c.data(
        "TAB6568", {"Region": admin_codes(), "Hushallstyp": ["TOTALT"], "ContentsCode": "*"}
    )
    c.add(_pick(df).select("code", "period", "value"), "households", "households", "TAB6568")


def fetch_migration(c: Collector) -> None:
    regions = admin_codes()
    years = [str(y) for y in range(2014, 2025)]
    young = [str(a) for a in range(20, 35)]
    hist = c.data(
        "TAB1212",
        {
            "Region": regions,
            "Alder": ["tot", *young],
            "Kon": ["1", "2"],
            "ContentsCode": ["BE0101A4", "BE0101A1"],
            "Tid": years,
        },
    )
    dom = hist.filter((pl.col("ContentsCode") == "BE0101A4") & (pl.col("Alder") == "tot"))
    c.add(_sum(dom), "dom_net", "persons", "TAB1212")
    intl = hist.filter((pl.col("ContentsCode") == "BE0101A1") & (pl.col("Alder") == "tot"))
    c.add(_sum(intl), "int_net", "persons", "TAB1212")
    dy = hist.filter((pl.col("ContentsCode") == "BE0101A4") & (pl.col("Alder") != "tot"))
    c.add(_sum(dy), "dom_net_20_34", "persons", "TAB1212")
    new = c.data(
        "TAB6640",
        {
            "Region": regions,
            "Alder": ["tot", "20-24", "25-29", "30-34"],
            "Kon": ["TotSa"],
            "ContentsCode": ["0000086C", "0000086A"],
        },
    )
    c.add(
        _pick(new, ContentsCode="0000086C", Alder="tot").select("code", "period", "value"),
        "dom_net",
        "persons",
        "TAB6640",
    )
    c.add(
        _pick(new, ContentsCode="0000086A", Alder="tot").select("code", "period", "value"),
        "int_net",
        "persons",
        "TAB6640",
    )
    c.add(
        _sum(new.filter((pl.col("ContentsCode") == "0000086C") & (pl.col("Alder") != "tot"))),
        "dom_net_20_34",
        "persons",
        "TAB6640",
    )


def fetch_construction(c: Collector) -> None:
    regions = admin_codes()
    t = c.scb.metadata("TAB4572")
    quarters = [q for q in t.codes["Tid"] if int(q[:4]) >= 2012]
    q = c.data("TAB4572", {"Region": regions, "Hustyp": "*", "ContentsCode": "*", "Tid": quarters})
    c.add(_sum(q.filter(pl.col("ContentsCode") == "BO0101A4")), "starts_q", "dwellings", "TAB4572")
    c.add(
        _sum(q.filter(pl.col("ContentsCode") == "BO0101A3")),
        "completions_q",
        "dwellings",
        "TAB4572",
    )
    t = c.scb.metadata("TAB4193")
    years = [y for y in t.codes["Tid"] if int(y) >= 2012]
    comp = c.data(
        "TAB4193",
        {
            "Region": regions,
            "Hustyp": "*",
            "Upplatelseform": "*",
            "ContentsCode": "*",
            "Tid": years,
        },
    )
    c.add(_sum(comp), "completions", "dwellings", "TAB4193")
    c.add(
        _sum(comp.filter(pl.col("Upplatelseform") == "1")),
        "completions_rental",
        "dwellings",
        "TAB4193",
    )
    t = c.scb.metadata("TAB824")
    years = [y for y in t.codes["Tid"] if int(y) >= 2015]
    stock = c.data(
        "TAB824",
        {
            "Region": regions,
            "Hustyp": "*",
            "Upplatelseform": "*",
            "ContentsCode": "*",
            "Tid": years,
        },
    )
    c.add(_sum(stock), "dwellings", "dwellings", "TAB824")
    c.add(
        _sum(stock.filter(pl.col("Upplatelseform") == "1")),
        "dwellings_rental",
        "dwellings",
        "TAB824",
    )
    c.add(
        _sum(stock.filter(pl.col("Upplatelseform") == "2")),
        "dwellings_condo",
        "dwellings",
        "TAB824",
    )


def fetch_regso_housing(c: Collector) -> None:
    df = c.data(
        "TAB6638",
        {"Region": "*", "Upplatelseform": "*", "ContentsCode": "*"},
        {"Region": "vs_RegSO2025"},
    )
    c.add(_sum(df), "dwellings", "dwellings", "TAB6638")
    c.add(
        _pick(df, Upplatelseform="1").select("code", "period", "value"),
        "dwellings_rental",
        "dwellings",
        "TAB6638",
    )
    c.add(
        _pick(df, Upplatelseform="2").select("code", "period", "value"),
        "dwellings_condo",
        "dwellings",
        "TAB6638",
    )


def fetch_rents(c: Collector) -> None:
    df = c.data(
        "TAB4603",
        {"Region": "*", "AntalRum": ["00"], "Hyresuppg": ["Ah_kvm"], "ContentsCode": ["00000126"]},
    )
    df = df.filter(pl.col("region").str.len_chars() == 4)
    c.add(
        df.rename({"region": "code"}).select("code", "period", "value"),
        "rent_sqm",
        "SEK per sq m and year",
        "TAB4603",
    )
    nat = c.data("TAB6259", {"Region": "*", "ContentsCode": ["000006I5"]})
    c.add(
        nat.rename({"region": "code"}).select("code", "period", "value"),
        "rent_sqm_all",
        "SEK per sq m and year",
        "TAB6259",
    )
    nb = c.data(
        "TAB6417",
        {
            "Region": "*",
            "Hyresattningsmod": ["1", "3"],
            "AntalRum": ["00"],
            "ContentsCode": ["000007GJ"],
        },
    )
    for model, ind in (("1", "newbuild_rent_negotiated"), ("3", "newbuild_rent_presumption")):
        d = nb.filter(pl.col("Hyresattningsmod") == model).rename({"region": "code"})
        c.add(d.select("code", "period", "value"), ind, "SEK per sq m and year", "TAB6417")


def fetch_prices(c: Collector) -> None:
    hs = c.data("TAB1156", {"Region": "*", "Fastighetstyp": ["32"], "ContentsCode": "*"})
    _kt(c, hs, "BO0501B2", "BO0501B3", "BO0501B1", "rental_block", "TAB1156")
    ind = c.data(
        "TAB1158", {"Region": "*", "Fastighetstyp": ["420426", "432"], "ContentsCode": "*"}
    )
    for typ, name in (("420426", "industrial"), ("432", "warehouse")):
        _kt(
            c,
            ind.filter(pl.col("Fastighetstyp") == typ),
            "BO0501G6",
            "BO0501G7",
            "BO0501G5",
            name,
            "TAB1158",
        )
    sh = c.data("TAB3656", {"Region": "*", "ContentsCode": ["BO0501AK", "BO0501AJ"]})
    sh = sh.filter(pl.col("region").str.len_chars() == 2)
    c.add(
        _pick(sh, ContentsCode="BO0501AK").select("code", "period", "value"),
        "house_kt",
        "ratio",
        "TAB3656",
    )
    c.add(
        _pick(sh, ContentsCode="BO0501AJ").select("code", "period", "value"),
        "house_price",
        "SEK thousands",
        "TAB3656",
    )
    # assessed value per industrial unit (manufacturing 420-426 and warehouse 432)
    t = c.scb.metadata("TAB3797")
    years = t.codes["Tid"][-6:]
    codes = [
        x
        for x in t.codes["Typkod"]
        if x in {"420", "421", "422", "423", "424", "425", "426", "432"}
    ]
    tx = c.data(
        "TAB3797",
        {
            "Region": admin_codes(),
            "Typkod": codes,
            "ContentsCode": ["BO0601B3", "BO0601S1"],
            "Tid": years,
        },
    )
    val = _sum(tx.filter(pl.col("ContentsCode") == "BO0601B3"))
    n = _sum(tx.filter(pl.col("ContentsCode") == "BO0601S1"))
    per = val.join(n, on=["code", "period"], suffix="_n").with_columns(
        pl.when(pl.col("value_n") > 0)
        .then(pl.col("value") / pl.col("value_n"))
        .otherwise(None)
        .alias("value")
    )
    c.add(
        per.select("code", "period", "value"),
        "industrial_assessed_per_unit",
        "SEK thousands",
        "TAB3797",
        note="derived: total assessed value / units, type codes 420-426 and 432",
    )
    c.add(n.select("code", "period", "value"), "industrial_units", "units", "TAB3797")


def _kt(
    c: Collector, df: pl.DataFrame, price: str, assessed: str, count: str, name: str, table: str
) -> None:
    p = _pick(df, ContentsCode=price).select("code", "period", pl.col("value").alias("p"))
    a = _pick(df, ContentsCode=assessed).select("code", "period", pl.col("value").alias("a"))
    n = _pick(df, ContentsCode=count).select("code", "period", "value")
    kt = p.join(a, on=["code", "period"]).with_columns(
        pl.when(pl.col("a") > 0).then(pl.col("p") / pl.col("a")).otherwise(None).alias("value")
    )
    c.add(
        kt.select("code", "period", "value"),
        f"{name}_kt",
        "ratio",
        table,
        note="derived: mean price / mean assessed value",
    )
    c.add(n, f"{name}_sales", "sales", table)


def fetch_labour(c: Collector) -> None:
    regions = admin_codes()
    groups = {"total": "A-U+US", "H": "H", "F": "F", "BC": "B+C"}
    for table, contents, note in (
        ("TAB3204", ("000002XH", "000002XI"), None),
        ("TAB3785", ("000005FG", "000005FH"), "preliminary"),
    ):
        t = c.scb.metadata(table)
        years = t.codes["Tid"] if table == "TAB3204" else t.codes["Tid"][-1:]
        df = c.data(
            table,
            {
                "Region": regions,
                "Kon": ["1+2"],
                "SNI2007": list(groups.values()),
                "Fodelseregion": ["tot"],
                "ContentsCode": list(contents),
                "Tid": years,
            },
        )
        for key, sni in groups.items():
            d = _pick(df, SNI2007=sni, ContentsCode=contents[0]).select("code", "period", "value")
            c.add(d, f"emp_work_{key}", "persons", table, note=note)
        d = _pick(df, SNI2007="A-U+US", ContentsCode=contents[1]).select("code", "period", "value")
        c.add(d, "emp_res_total", "persons", table, note=note)
    t = c.scb.metadata("TAB1830")
    year = t.codes["Tid"][-1]
    m = c.data(
        "TAB1830",
        {
            "Kon": ["1+2"],
            "Bostadskommun": "*",
            "Arbetsstallekommun": "*",
            "ContentsCode": "*",
            "Tid": [year],
        },
    )
    m = m.filter(pl.col("Bostadskommun") != pl.col("Arbetsstallekommun"))
    inn = (
        m.group_by("Arbetsstallekommun", "period")
        .agg(pl.col("value").sum())
        .rename({"Arbetsstallekommun": "code"})
    )
    out = (
        m.group_by("Bostadskommun", "period")
        .agg(pl.col("value").sum())
        .rename({"Bostadskommun": "code"})
    )
    c.add(inn, "commuters_in", "persons", "TAB1830")
    c.add(out, "commuters_out", "persons", "TAB1830")
    grp = c.data("TAB3143", {"Region": "*", "ContentsCode": ["NR0105AW", "NR0105AY"]})
    grp = grp.filter(pl.col("region") != "9900")
    c.add(
        _pick(grp, ContentsCode="NR0105AW").select("code", "period", "value"),
        "grp",
        "SEK m, current prices",
        "TAB3143",
    )
    c.add(
        _pick(grp, ContentsCode="NR0105AY").select("code", "period", "value"),
        "grp_per_capita",
        "SEK thousands, current prices",
        "TAB3143",
    )


def fetch_income_education(c: Collector) -> None:
    regions = admin_codes()
    t = c.scb.metadata("TAB3554")
    years = t.codes["Tid"][-11:]
    inc = c.data(
        "TAB3554",
        {
            "Region": regions,
            "Kon": ["1+2"],
            "Alder": ["tot20+"],
            "Inkomstklass": ["TOT"],
            "ContentsCode": ["HE0110J8"],
            "Tid": years,
        },
    )
    c.add(_pick(inc).select("code", "period", "value"), "income_median", "SEK thousands", "TAB3554")
    net_sel = {"Inkomstkomponenter": ["240"], "Kon": ["1+2"], "ContentsCode": ["000008A4"]}
    for reg, cl in ((regions, None), ("*", "vs_RegSO2025"), ("*", "vs_RegSO2020")):
        df = c.data("TAB6683", {"Region": reg, **net_sel}, {"Region": cl} if cl else None)
        d = _pick(df).select("code", "period", "value")
        if cl == "vs_RegSO2020":
            d = d.with_columns(pl.lit("RegSO 2020 borders").alias("note"))
        c.add(d, "net_income_mean", "SEK thousands, 2024 prices", "TAB6683")
    for reg, cl in ((regions, None), ("*", "vs_RegSO2025"), ("*", "vs_RegSO2020")):
        df = c.data(
            "TAB6685",
            {"Region": reg, "Alder": ["tot"], "ContentsCode": ["000008AC", "000008AD"]},
            {"Region": cl} if cl else None,
        )
        for cc, ind in (("000008AC", "low_econ_std"), ("000008AD", "high_econ_std")):
            d = _pick(df, ContentsCode=cc).select("code", "period", "value")
            if cl == "vs_RegSO2020":
                d = d.with_columns(pl.lit("RegSO 2020 borders").alias("note"))
            c.add(d, ind, "percent", "TAB6685")
    for reg, cl in ((regions, None), ("*", "vs_RegSO2025")):
        df = c.data(
            "TAB6534",
            {"Region": reg, "UtbildningsNiva": "*", "ContentsCode": "*"},
            {"Region": cl} if cl else None,
        )
        c.add(_sum(df), "pop_25_65", "persons", "TAB6534")
        c.add(
            _pick(df, UtbildningsNiva="6").select("code", "period", "value"),
            "edu_post3",
            "persons",
            "TAB6534",
        )


def fetch_regso_status(c: Collector) -> None:
    for cl, note in (("vs_RegSO2025", None), ("vs_RegSO2020", "RegSO 2020 borders")):
        sei = c.data(
            "TAB6586", {"Region": "*", "ContentsCode": ["000008IT", "000008IU"]}, {"Region": cl}
        )
        c.add(
            _pick(sei, ContentsCode="000008IT").select("code", "period", "value"),
            "sei",
            "index",
            "TAB6586",
            note=note,
        )
        c.add(
            _pick(sei, ContentsCode="000008IU").select("code", "period", "value"),
            "area_type",
            "class 1-5",
            "TAB6586",
            note=note,
        )
    df = c.data(
        "TAB6680",
        {
            "Region": "*",
            "Kon": ["1+2"],
            "Alder": ["20-64"],
            "ContentsCode": ["0000089X", "0000089Y"],
        },
        {"Region": "vs_RegSO2025"},
    )
    e = _pick(df, ContentsCode="0000089X").select("code", "period", pl.col("value").alias("e"))
    n = _pick(df, ContentsCode="0000089Y").select("code", "period", pl.col("value").alias("n"))
    rate = e.join(n, on=["code", "period"]).with_columns(
        pl.when(pl.col("n") > 0).then(pl.col("e") / pl.col("n")).otherwise(None).alias("value")
    )
    c.add(
        rate.select("code", "period", "value"),
        "emp_rate",
        "share",
        "TAB6680",
        note="derived: employed / population, 20-64",
    )
    mig = c.data("TAB5724", {"Region": "*", "Bakgrund": ["1+2"], "ContentsCode": ["000004V8"]})
    c.add(
        _pick(mig)
        .select("code", "period", "value")
        .with_columns(pl.lit("RegSO 2020 borders").alias("note")),
        "dom_net_pct",
        "percent",
        "TAB5724",
    )


def fetch_misc(c: Collector) -> None:
    regions = admin_codes()
    tr = c.data(
        "TAB4947",
        {
            "Region": regions,
            "AvstandKoll": ["500"],
            "Bostadsbestand": ["100"],
            "ContentsCode": ["0000024V"],
        },
    )
    c.add(_pick(tr).select("code", "period", "value"), "transit_500m", "percent", "TAB4947")
    t = c.scb.metadata("TAB694")
    pr = c.data(
        "TAB694",
        {
            "Region": "*",
            "ContentsCode": ["000004KO"],
            "Tid": [y for y in t.codes["Tid"] if y in ("2025", "2030", "2035")],
        },
    )
    c.add(
        _pick(pr).select("code", "period", "value"),
        "pop_projection",
        "persons",
        "TAB694",
        note="projection",
    )
    land = c.data(
        "TAB6621", {"Region": regions, "Byggnadstyp": ["2"], "ContentsCode": ["00000830"]}
    )
    c.add(_pick(land).select("code", "period", "value"), "industrial_land", "1,000 sq m", "TAB6621")


# --------------------------------------------------------------------------- Kolada

KOLADA_KPIS = {
    "U30446": ("bme", "0 shortage, 1 balance, 2 surplus"),
    "U30457": ("bme_students", "0 shortage, 1 balance, 2 surplus"),
    "U30460": ("bme_young", "0 shortage, 1 balance, 2 surplus"),
    "N03937": ("unemployment", "percent of 18-65"),
    "N00904": ("tax_base_pct", "percent of national average"),
}


def fetch_kolada(f: Fetcher, c: Collector) -> pl.DataFrame:
    year = date.today().year
    for kpi, (ind, unit) in KOLADA_KPIS.items():
        df = kolada.kpi_values(f, kpi, list(range(year - 12, year + 2)))
        c.as_of[kpi] = date.today().isoformat()
        c.add(
            df.select("code", "period", "value"),
            ind,
            unit,
            kpi,
            source=KOLADA_SOURCE,
            url=kolada.kpi_url(kpi),
        )
    return kolada.peer_groups(f)


# --------------------------------------------------------------------------- geography


def _nodes() -> dict[str, list[dict]]:
    return yaml.safe_load((GEO_DIR / "logistics_nodes.yaml").read_text("utf-8"))


def _junctions() -> pl.DataFrame | None:
    p = GEO_DIR / "motorway_junctions.csv"
    return pl.read_csv(p) if p.exists() else None


def geography(f: Fetcher, out_dir, c: Collector) -> tuple[pl.DataFrame, dict]:
    """location rows (without population) and the raw geo dict; adds geo indicators."""
    from headroom.sources import scb_geo

    g = scb_geo.build(f, out_dir)
    k = kommuner()
    kname = dict(zip(k["kommun_code"], k["kommun"], strict=True))
    lname = dict(zip(k["county_code"], k["county"], strict=True))
    today = date.today().isoformat()
    rows = [
        {
            "level": "riket",
            "code": "00",
            "name": "Sweden",
            "display_name": "Sweden",
            "parent_kommun": None,
            "parent_lan": None,
            "regso_version": None,
            "tatort_codes": None,
            "lat": 62.0,
            "lon": 15.0,
        }
    ]
    for code, v in sorted(g["lan"].items()):
        rows.append(
            {
                "level": "lan",
                "code": code,
                "name": lname.get(code, code),
                "display_name": lname.get(code, code),
                "parent_kommun": None,
                "parent_lan": None,
                "regso_version": None,
                "tatort_codes": None,
                "lat": v["lat"],
                "lon": v["lon"],
            }
        )
    for code, v in sorted(g["kommun"].items()):
        rows.append(
            {
                "level": "kommun",
                "code": code,
                "name": kname.get(code, code),
                "display_name": kname.get(code, code),
                "parent_kommun": None,
                "parent_lan": code[:2],
                "regso_version": None,
                "tatort_codes": None,
                "lat": v["lat"],
                "lon": v["lon"],
            }
        )
    regso_tatort: dict[str, list[str]] = {}
    for t in g["tatort"]:
        for r in t["regso"]:
            regso_tatort.setdefault(r, []).append(t["code"])
        rows.append(
            {
                "level": "tatort",
                "code": t["code"],
                "name": t["name"],
                "display_name": t["name"],
                "parent_kommun": t["kommun"],
                "parent_lan": t["kommun"][:2],
                "regso_version": None,
                "tatort_codes": ",".join(t["regso"]),
                "lat": t["lat"],
                "lon": t["lon"],
            }
        )
    for r in g["regso"]:
        short = r["name"].removeprefix("RegSO-").strip()
        rows.append(
            {
                "level": "regso",
                "code": r["code"],
                "name": short,
                "display_name": f"{kname.get(r['kommun'], r['kommun'])} ({short})",
                "parent_kommun": r["kommun"],
                "parent_lan": r["lan"],
                "regso_version": "2025",
                "tatort_codes": ",".join(regso_tatort.get(r["code"], [])) or None,
                "lat": r["lat"],
                "lon": r["lon"],
            }
        )
    loc = pl.DataFrame(rows, infer_schema_length=None)

    # population within 50/100/200 km, distances to logistics nodes (straight line)
    from headroom.sources.scb_geo import wgs84_to_grid, within

    pts = [("00", np.nan, np.nan)]
    pts += [(code, v["cx"], v["cy"]) for code, v in g["lan"].items()]
    pts += [(code, v["cx"], v["cy"]) for code, v in g["kommun"].items()]
    pts += [(r["code"], r["cx"], r["cy"]) for r in g["regso"]]
    codes = [p[0] for p in pts[1:]]
    cx = np.array([p[1] for p in pts[1:]])
    cy = np.array([p[2] for p in pts[1:]])
    year = "2025"
    for km in (50, 100, 200):
        pop = within(cx, cy, g["grid"], km * 1000)
        c.as_of[f"catchment_{km}km"] = today
        c.add(
            pl.DataFrame({"code": codes, "period": year, "value": pop}),
            f"catchment_{km}km",
            "persons",
            "befolkning_1km_2025",
            source=GEO_SOURCE,
            url=scb_geo.GEODATA_PAGE,
            note="straight-line radius from the population-weighted centre; "
            "approximates drive time",
        )
    nodes = _nodes()
    groups = {
        "port": nodes.get("ports", []),
        "terminal": nodes.get("intermodal_terminals", []),
        "airport": nodes.get("cargo_airports", []),
    }
    j = _junctions()
    if j is not None and j.height:
        groups["motorway"] = [
            {
                "name": f"Junction {r['ref'] or ''} {r['name'] or ''}".strip(),
                "lat": r["lat"],
                "lon": r["lon"],
            }
            for r in j.to_dicts()
        ]
    for kind, items in groups.items():
        if not items:
            continue
        ne, nn = wgs84_to_grid([n["lat"] for n in items], [n["lon"] for n in items])
        d = np.sqrt((cx[:, None] - ne[None, :]) ** 2 + (cy[:, None] - nn[None, :]) ** 2)
        best = d.argmin(axis=1)
        dist = d[np.arange(len(codes)), best] / 1000
        names = [items[i]["name"] for i in best]
        src = (
            "OpenStreetMap"
            if kind == "motorway"
            else "config/geo/logistics_nodes.yaml (Wikidata, OpenStreetMap)"
        )
        c.as_of[f"dist_{kind}"] = today
        c.add(
            pl.DataFrame({"code": codes, "period": year, "value": dist, "note": names}),
            f"dist_{kind}",
            "km",
            f"dist_{kind}",
            source=src,
            url="https://www.openstreetmap.org/copyright" if kind == "motorway" else None,
        )
    biz = g["business_areas"]
    c.as_of["Verksamhetsomraden_2020"] = today
    c.add(
        pl.DataFrame(
            {"code": list(biz), "period": "2020", "value": [b["employees"] for b in biz.values()]}
        ),
        "business_area_employees",
        "persons",
        "Verksamhetsomraden_2020",
        source=GEO_SOURCE,
        url=scb_geo.GEODATA_PAGE,
    )
    c.add(
        pl.DataFrame(
            {"code": list(biz), "period": "2020", "value": [b["area_ha"] for b in biz.values()]}
        ),
        "business_area_ha",
        "hectares",
        "Verksamhetsomraden_2020",
        source=GEO_SOURCE,
        url=scb_geo.GEODATA_PAGE,
    )
    tat = [t for t in g["tatort"] if t["pop"] is not None]
    c.as_of["Tatorter_2023"] = today
    c.add(
        pl.DataFrame(
            {
                "code": [t["code"] for t in tat],
                "period": [str(t["year"]) for t in tat],
                "value": [float(t["pop"]) for t in tat],
            }
        ),
        "pop",
        "persons",
        "Tatorter_2023",
        source=GEO_SOURCE,
        url=scb_geo.GEODATA_PAGE,
    )
    return loc, g


# --------------------------------------------------------------------------- groups


def groups(c: Collector) -> dict[str, dict[str, str]]:
    """kommun -> SKR kommungrupp 2023, LA 2018 region, storstadsområde."""
    out: dict[str, dict[str, str]] = {"kommungrupp": {}, "la": {}, "la_name": {}, "storstad": {}}
    for cl, key in (
        ("agg_RegionKommungrupp2023-", "kommungrupp"),
        ("agg_RegionLA2018", "la"),
        ("agg_RegionStoromr05-_1", "storstad"),
    ):
        try:
            for v in c.scb.codelist(cl):
                for k in v.get("valueMap", []):
                    out[key][k] = v["label"] if key == "kommungrupp" else v["code"]
                    if key == "la":
                        out["la_name"][k] = v["label"].strip()
        except Exception as e:
            log.warning("codelist %s unavailable: %s", cl, e)
    return out


# --------------------------------------------------------------------------- build


def build_locations() -> dict:
    """Fetch everything, derive scores and peers, write data/snapshots/locations/."""
    from headroom.config import weights as load_weights
    from headroom.model import location as L

    out_dir = snapshot_dir("locations")
    out_dir.mkdir(parents=True, exist_ok=True)
    with Fetcher() as f:
        scb = SCB(f)
        c = Collector(scb)
        loc, _g = geography(f, out_dir, c)
        for step in (
            fetch_population,
            fetch_households,
            fetch_migration,
            fetch_construction,
            fetch_regso_housing,
            fetch_rents,
            fetch_prices,
            fetch_labour,
            fetch_income_education,
            fetch_regso_status,
            fetch_misc,
        ):
            log.info("Locations: %s", step.__name__)
            step(c)
        peers_kolada = fetch_kolada(f, c)
        grp = groups(c)
    ind = c.frame()
    k = kommuner()
    name_to_code = dict(zip(k["kommun"], k["kommun_code"], strict=True))
    loc = loc.with_columns(
        pl.col("code").replace_strict(grp["kommungrupp"], default=None).alias("kommungrupp"),
        pl.col("code").replace_strict(grp["la"], default=None).alias("la_region"),
        pl.col("code").replace_strict(grp["la_name"], default=None).alias("la_name"),
        pl.col("code").replace_strict(grp["storstad"], default=None).alias("storstad"),
    )
    latest_pop = (
        ind.filter(pl.col("indicator") == "pop")
        .sort("period")
        .group_by("code")
        .agg(
            pl.col("value").drop_nulls().last().alias("population"),
            pl.col("period").filter(pl.col("value").is_not_null()).last().alias("population_year"),
        )
    )
    loc = loc.join(latest_pop, on="code", how="left")
    utsatta = L.load_utsatta()
    feats = L.features(loc, ind, utsatta)
    cfg = load_weights()
    scores = L.score_all(feats, cfg)
    kp = peers_kolada.with_columns(
        pl.col("name").replace_strict(name_to_code, default=None).alias("code")
    ).drop_nulls("code")
    peers = L.peer_table(loc, feats, kp)
    as_of = {**c.as_of, "generated": date.today().isoformat()}
    meta_extra = {
        "scb_updated": {t: m.updated for t, m in scb._meta.items()},
        "counts": {
            lvl: int((loc["level"] == lvl).sum()) for lvl in ("lan", "kommun", "tatort", "regso")
        },
        "regso_version": "2025 (statistics from 2024); 2020 for earlier years",
        "kolada_peer_groups": peers_kolada["title"].unique().len() if peers_kolada.height else 0,
    }
    write_snapshot(
        "locations",
        {
            "location": loc,
            "location_indicator": ind,
            "location_score": scores,
            "location_peer": peers,
        },
        fictional=False,
        as_of=as_of,
        notes="Public statistics for Locations: SCB (Statistikdatabasen, öppna geodata), Kolada, "
        "Polisen, OpenStreetMap and Wikidata. The same data in live and demo mode.",
        extra=meta_extra,
    )
    log.info("Locations snapshot: %d places, %d indicator rows", loc.height, ind.height)
    return {
        "places": loc.height,
        "indicators": ind.height,
        "generated_at": datetime.now(UTC).isoformat(),
    }
