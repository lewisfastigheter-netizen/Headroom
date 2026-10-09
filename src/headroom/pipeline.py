"""Build the live snapshot from public sources.

1. Bond universe: ESMA FIRDS + GLEIF, listed-equity flag from FIRDS equities.
2. Rates and FX: Sveriges Riksbank.
3. Reports: MFN/Cision feeds, latest interim reports read by an LLM into
   financials with field-level provenance. Share prices from Yahoo Finance.
Later milestones add private companies, events and covenants to the same snapshot.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

import polars as pl

from headroom.config import settings
from headroom.config import weights as load_weights
from headroom.extract import events, kpi
from headroom.extract.documents import fetch_pdf
from headroom.extract.llm import LLMUnavailable
from headroom.http import Fetcher
from headroom.model import geo
from headroom.model.universe import classify
from headroom.sources import firds, gleif, newsfeeds, prices, riksbank
from headroom.store.db import snapshot_dir, write_snapshot
from headroom.store.private import PRIVATE_CANDIDATES, PRIVATE_LEDGER, load_found, save_found

log = logging.getLogger(__name__)

FIRDS_FILE_URL = "https://firds.esma.europa.eu/firds/{}"


def build_bond_universe(
    f: Fetcher, as_of: date
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict]:
    """Returns (company, bond, issuer_audit, meta)."""
    venue_rows, debt_meta = firds.load_universe(f, as_of=as_of)
    bonds = firds.dedupe(venue_rows)
    log.info(
        "FIRDS: %d candidate ISINs from %d issuers", bonds.height, bonds["issuer_lei"].n_unique()
    )

    issuers = gleif.lookup_leis(f, bonds["issuer_lei"].drop_nulls().unique().to_list())
    swedish = issuers.filter(pl.col("country") == "SE")
    audit = swedish.with_columns(
        pl.struct("legal_name", "lei")
        .map_elements(lambda r: classify(r["legal_name"], r["lei"]), return_dtype=pl.Utf8)
        .alias("universe_rule")
    )
    prop = audit.filter(pl.col("universe_rule").is_not_null() & pl.col("org_nr").is_not_null())
    log.info("GLEIF: %d Swedish issuers, %d property companies", swedish.height, prop.height)

    shares, eq_meta = firds.listed_shares(f, as_of=as_of)
    listed_leis = set(shares["issuer_lei"].drop_nulls().to_list()) if shares.height else set()

    # Listed property companies without listed bonds (bank-financed) are in the
    # universe too: classify every Swedish issuer of shares on a Swedish venue.
    new_leis = sorted(listed_leis - set(issuers["lei"].to_list()))
    if new_leis:
        eq_issuers = gleif.lookup_leis(f, new_leis).filter(pl.col("country") == "SE")
        eq_audit = eq_issuers.with_columns(
            pl.struct("legal_name", "lei")
            .map_elements(lambda r: classify(r["legal_name"], r["lei"]), return_dtype=pl.Utf8)
            .alias("universe_rule")
        )
        audit = pl.concat([audit, eq_audit], how="diagonal_relaxed")
        prop = audit.filter(pl.col("universe_rule").is_not_null() & pl.col("org_nr").is_not_null())
        log.info("GLEIF: %d property companies incl. share-only issuers", prop.height)

    bonds = bonds.join(
        prop.select("lei", "org_nr"), left_on="issuer_lei", right_on="lei", how="inner"
    )
    fx = riksbank.latest_fx(f, set(bonds["currency"].drop_nulls().unique().to_list()))
    rate_of = {k: v["rate"] for k, v in fx.items()}
    pub = date.fromisoformat(debt_meta["publication_date"])

    bond_out = bonds.select(
        "isin",
        "org_nr",
        "full_name",
        (pl.col("nominal") / 1e6).alias("nominal"),
        pl.col("nominal_ccy").fill_null(pl.col("currency")).alias("currency"),
        (
            pl.col("nominal")
            / 1e6
            * pl.col("nominal_ccy")
            .fill_null(pl.col("currency"))
            .replace_strict(rate_of, default=None)
        ).alias("nominal_sek"),
        pl.col("first_trade").alias("issue_date"),
        "maturity",
        "coupon_type",
        pl.when(pl.col("benchmark").is_not_null())
        .then(pl.col("benchmark") + pl.lit(" ") + pl.col("benchmark_term").fill_null(""))
        .otherwise(None)
        .str.strip_chars()
        .alias("benchmark"),
        "margin_bp",
        "coupon_pct",
        "secured",
        pl.col("venues").list.join(", ").alias("venue"),
        pl.lit(None, dtype=pl.Utf8).alias("trustee"),
        pl.col("source_file")
        .map_elements(FIRDS_FILE_URL.format, return_dtype=pl.Utf8)
        .alias("source_url"),
        pl.lit(pub).alias("as_of"),
    )

    company = prop.select(
        "org_nr",
        "lei",
        pl.col("legal_name").alias("name"),
        pl.when(pl.col("lei").is_in(list(listed_leis)))
        .then(pl.lit("listed"))
        .otherwise(pl.lit("bond"))
        .alias("tier"),
        pl.lit(None, dtype=pl.Utf8).alias("listed_ticker"),
        pl.lit(None, dtype=pl.Utf8).alias("sni"),
        pl.lit(None, dtype=pl.Utf8).alias("segment_mix"),
        pl.lit(None, dtype=pl.Utf8).alias("region_mix"),
        pl.lit(None, dtype=pl.Float64).alias("size"),
        "universe_rule",
        "source_url",
        pl.lit(as_of).alias("as_of"),
    ).filter(
        pl.col("org_nr").is_in(bond_out["org_nr"].unique().to_list()) | (pl.col("tier") == "listed")
    )
    # One row per org number (an entity can hold several LEIs only in error, but be safe).
    company = company.unique("org_nr", keep="first")

    meta = {
        "firds_debt": debt_meta,
        "firds_equity": eq_meta,
        "fx": fx,
        "issuers_swedish": swedish.height,
        "issuers_property": company.height,
    }
    return company, bond_out, audit, meta


REPORT_PDF_HINT = re.compile(
    r"interim|report|delars|delårs|bokslut|halvars|q[1-4]|kvartal|year-end|arsredovisning|annual",
    re.IGNORECASE,
)


def _pick_pdf(attachments: list[str]) -> str | None:
    pdfs = [a for a in attachments if a.lower().endswith(".pdf")]
    if not pdfs:
        return None
    hinted = [a for a in pdfs if REPORT_PDF_HINT.search(a.rsplit("/", 1)[-1])]
    english = [a for a in hinted if re.search(r"interim|report|year-end|annual", a, re.I)]
    # Cision: /Public/ holds the attached report, /Main/ is the release itself as PDF
    public = [a for a in pdfs if "/Public/" in a]
    return (english or hinted or public or pdfs)[0]


def _best_report_pdf(f: Fetcher, pdfs: list[str], max_try: int = 3):
    """Among a release's PDFs, the full report: highest key-figure score, at least 8 pages.

    Releases often attach the press release itself as a PDF next to the report.
    """
    from headroom.extract.documents import score_page

    first = _pick_pdf(pdfs)
    ordered = ([first] if first else []) + [p for p in pdfs if p != first]
    best, best_score = None, 0
    for url in ordered[:max_try]:
        try:
            doc = fetch_pdf(f, url)
        except Exception as e:
            log.warning("pdf %s: %s", url, e)
            continue
        score = sum(sorted((score_page(t) for t in doc.pages), reverse=True)[:10])
        if doc.n_pages >= 8 and score > best_score:
            best, best_score = doc, score
    return best


def build_reports(
    f: Fetcher, company: pl.DataFrame, fx: dict[str, dict], reports_per_company: int = 2
) -> dict[str, pl.DataFrame | dict]:
    """Feeds, releases, KPI extraction and prices for every company in the universe."""
    feeds, releases_rows, fin_rows, prov_rows, enrich_rows, nav_rows = [], [], [], [], [], []
    event_rows: list[dict] = []
    cutoff = datetime.now() - timedelta(days=730)
    llm_ok, llm_errors, extracted = settings().llm_available, [], 0
    rate_of = {k: v["rate"] for k, v in fx.items()}
    for co in company.to_dicts():
        feed = newsfeeds.find_feed(f, co["org_nr"], co["lei"], co["name"])
        if not feed:
            continue
        ticker = newsfeeds.yahoo_ticker(feed.tickers) if co["tier"] == "listed" else None
        feeds.append(
            {
                "org_nr": co["org_nr"],
                "provider": feed.provider,
                "slug": feed.slug,
                "url": feed.url,
                "matched_on": feed.matched_on,
                "ticker": ticker,
            }
        )
        try:
            rels = newsfeeds.releases(f, feed)
        except Exception as e:
            log.warning("feed %s failed: %s", feed.url, e)
            continue
        releases_rows.extend(r.to_row() for r in rels)
        if llm_ok:
            for r in rels:
                if not events.is_candidate(r.title):
                    continue
                try:
                    r = newsfeeds.release_detail(f, r)
                    if r.published and r.published.replace(tzinfo=None) < cutoff:
                        continue
                    ev = events.to_event(r, events.classify(r))
                except LLMUnavailable:
                    llm_ok = False
                    break
                except Exception as e:
                    log.warning("event %s failed: %s", r.url, e)
                    continue
                if ev:
                    event_rows.append(ev)
        # Interim reports first; annual reports fill in for issuers that publish few interims.
        reports = [r for r in rels if newsfeeds.is_period_report(r.title)] + [
            r for r in rels if newsfeeds.is_annual_report(r.title)
        ]
        chosen, seen_pdf = [], set()
        for r in reports:
            r = newsfeeds.release_detail(f, r)
            if r.published and r.published.replace(tzinfo=None) < cutoff:
                continue  # older than two years: not a current picture
            rep_ev = events.report_event(r)
            if rep_ev:
                event_rows.append(rep_ev)
            pdfs = [a for a in r.attachments if a.lower().endswith(".pdf") and a not in seen_pdf]
            if not pdfs:
                continue
            seen_pdf.update(pdfs)
            chosen.append((r, pdfs))
            if len(chosen) >= reports_per_company * 2:  # EN and SV versions of the same report
                break
        done_periods: set[date] = set()
        for r, pdfs in chosen:
            if len(done_periods) >= reports_per_company or not llm_ok:
                break
            try:
                doc = _best_report_pdf(f, pdfs)
                if doc is None:
                    continue
                pdf = doc.url
                k, _meta, _ = kpi.extract_report(doc, co["name"])
            except LLMUnavailable:
                llm_ok = False
                break
            except Exception as e:
                llm_errors.append(f"{co['name']}: {e}")
                log.warning("extract %s failed: %s", pdf, e)
                continue
            if not k.is_financial_report or len(k.figures) < 3:
                continue
            try:
                pe = date.fromisoformat(k.period_end)
            except ValueError:
                llm_errors.append(f"{co['name']}: no period end in {pdf}")
                continue
            if pe in done_periods:
                continue
            done_periods.add(pe)
            rate = rate_of.get(k.currency.upper())
            if rate is None:
                log.warning("no FX for %s", k.currency)
                continue
            try:
                ptype = "FY" if newsfeeds.is_annual_report(r.title) else "Q"
                row, prov, enrich = kpi.to_rows(k, co["org_nr"], pdf, rate, period_type=ptype)
            except Exception as e:
                llm_errors.append(f"{co['name']}: mapping failed: {e}")
                continue
            fin_rows.append(row)
            prov_rows.extend(prov)
            extracted += 1
            if not enrich_rows or enrich_rows[-1]["org_nr"] != co["org_nr"]:
                enrich_rows.append(
                    {
                        "org_nr": co["org_nr"],
                        **{x: enrich[x] for x in ("segment_mix", "region_mix", "size")},
                        "ticker": ticker,
                    }
                )
            if ticker and enrich["nav_per_share"]:
                nav_rows.append(
                    {
                        "ticker": ticker,
                        "period_end": pe,
                        "report_date": r.published.date() if r.published else pe,
                        "nav_per_share": enrich["nav_per_share"],
                    }
                )
    tickers = [x["ticker"] for x in feeds if x["ticker"]]
    px = prices.history(tickers, date.today() - timedelta(days=2 * 365)) if tickers else None
    if px is not None and nav_rows:
        px = prices.attach_nav(px, pl.DataFrame(nav_rows))
    return {
        "feeds": pl.DataFrame(feeds),
        "releases": pl.DataFrame(releases_rows) if releases_rows else pl.DataFrame(),
        "event": pl.DataFrame(events.dedupe_languages(event_rows)) if event_rows else None,
        "financials": pl.DataFrame(fin_rows, infer_schema_length=None) if fin_rows else None,
        "provenance": pl.DataFrame(prov_rows, infer_schema_length=None) if prov_rows else None,
        "enrich": pl.DataFrame(enrich_rows, infer_schema_length=None) if enrich_rows else None,
        "price": px,
        "meta": {
            "feeds": len(feeds),
            "reports_extracted": extracted,
            "llm_available": llm_ok,
            "events": len(event_rows),
            "llm_errors": llm_errors[:20],
            "tickers": len(tickers),
        },
    }


def _apply_enrichment(company: pl.DataFrame, rep: dict) -> pl.DataFrame:
    feeds = rep["feeds"]
    if feeds.height:
        company = company.drop("listed_ticker").join(
            feeds.select("org_nr", pl.col("ticker").alias("listed_ticker")), on="org_nr", how="left"
        )
    enrich = rep.get("enrich")
    if enrich is not None and enrich.height:
        e = enrich.select(
            "org_nr",
            pl.col("segment_mix").alias("_seg"),
            pl.col("region_mix").alias("_reg"),
            pl.col("size").alias("_size"),
        )
        company = (
            company.join(e, on="org_nr", how="left")
            .with_columns(
                pl.coalesce("_seg", "segment_mix").alias("segment_mix"),
                pl.coalesce("_reg", "region_mix").alias("region_mix"),
                pl.coalesce("_size", "size").alias("size"),
            )
            .drop("_seg", "_reg", "_size")
        )
    cols = [
        "org_nr",
        "lei",
        "name",
        "tier",
        "listed_ticker",
        "sni",
        "segment_mix",
        "region_mix",
        "size",
        "universe_rule",
        "source_url",
        "as_of",
    ]
    return company.select(cols)


def build_live(as_of: date | None = None, with_reports: bool = True) -> dict:
    as_of = as_of or date.today()
    with Fetcher() as f:
        rates = riksbank.fetch_rates(f, as_of - timedelta(days=3 * 365), as_of)
        company, bond, audit, meta = build_bond_universe(f, as_of)
        tables: dict[str, pl.DataFrame] = {"company": company, "bond": bond, "rate": rates}
        as_of_map = {
            "company": as_of.isoformat(),
            "bond": meta["firds_debt"]["publication_date"],
            "rate": str(rates["date"].max()),
        }
        rep = None
        if with_reports:
            fx = riksbank.latest_fx(f, {"EUR", "NOK", "DKK", "USD"})
            rep = build_reports(f, company, fx)
            tables["company"] = _apply_enrichment(company, rep)
            if rep["financials"] is not None:
                tables["financials"] = rep["financials"]
                tables["provenance"] = rep["provenance"]
                as_of_map["financials"] = str(rep["financials"]["period_end"].max())
            if rep["event"] is not None:
                tables["event"] = rep["event"]
                as_of_map["event"] = as_of.isoformat()
            if rep["price"] is not None and rep["price"].height:
                tables["price"] = rep["price"]
                as_of_map["price"] = str(rep["price"]["date"].max())
            meta["reports"] = rep["meta"]
        # Bolagsverket: SNI codes for issuers and the private-AB universe (needs credentials)
        try:
            priv = build_private(
                f,
                tables["company"],
                settings().bolagsverket_bulkfile,
                as_of,
                max_new=int(
                    (load_weights().get("private_universe") or {}).get("max_lookups_in_refresh", 0)
                ),
            )
        except Exception as e:  # NoCredentials, or API errors: the rest of the snapshot stands
            log.info("Bolagsverket stage skipped: %s", e)
            priv = None
        if priv is not None:
            if priv["sni"] is not None:
                cols = tables["company"].columns
                tables["company"] = (
                    tables["company"]
                    .drop([c for c in ("sni", "county", "city") if c in cols])
                    .join(
                        priv["sni"].select("org_nr", "sni", "county", "city"),
                        on="org_nr",
                        how="left",
                    )
                )
            for name in ("company", "financials", "event"):
                extra = priv[name]
                if extra is not None and extra.height:
                    base = tables.get(name)
                    tables[name] = (
                        pl.concat([base, extra], how="diagonal_relaxed").select(base.columns)
                        if base is not None
                        else extra
                    )
            meta["bolagsverket"] = priv["meta"]
    path = write_snapshot(
        "live",
        tables,
        fictional=False,
        as_of=as_of_map,
        notes="Live snapshot. Bond universe from ESMA FIRDS and GLEIF; rates from Sveriges "
        "Riksbank; reports from MFN and Cision, read by an LLM; prices from Yahoo Finance.",
        extra={"sources": meta},
    )
    live_dir = snapshot_dir("live")
    audit.write_parquet(live_dir / "issuer_audit.parquet")
    if rep is not None:
        rep["feeds"].write_parquet(live_dir / "feeds.parquet")
        if rep["releases"].height:
            rep["releases"].write_parquet(live_dir / "releases.parquet")
    log.info("Live snapshot written to %s", path)
    return meta


# --------------------------------------------------------------------------- private ABs


def update_private(as_of: date | None = None, max_new: int | None = None) -> dict:
    """Daily job: check the next batch of private candidates. Results go to
    data/private_checked.csv and data/private_found.jsonl; the app merges the kept
    companies into the live data, so no snapshot file is touched."""
    from headroom.store.db import open_snapshot

    as_of = as_of or date.today()
    listed = open_snapshot("live").table("company").filter(pl.col("tier") != "private")
    f = Fetcher()
    priv = build_private(f, listed, settings().bolagsverket_bulkfile, as_of, max_new)
    _backfill_locations(f)
    return priv["meta"]


def _backfill_locations(f: Fetcher) -> None:
    """Companies found before county and city were recorded get them from the API."""
    from headroom.sources import bolagsverket as bv

    found = load_found()
    todo = [o for o, r in found.items() if "county" not in r["company"]]
    if not todo:
        return
    api = bv.Bolagsverket(f)
    for org in todo:
        try:
            county, city = geo.from_address(*bv.address(api.organisation(org)))
        except Exception as e:
            log.warning("address %s: %s", org, e)
            continue
        found[org]["company"].update(county=county, city=city)
    save_found(found)


# How long a negative result stands before the company is looked at again.
RECHECK_DAYS = {
    "not_real_estate": 365,
    "no_digital_report": 180,
    "no_ixbrl": 180,
    "below_threshold": 180,
}


def _old_enough(r: dict, months: int = 15) -> bool:
    """Companies registered in the last ~15 months have not filed an annual report yet."""
    reg = str(r.get("registered") or "")[:10]
    try:
        return (date.today() - date.fromisoformat(reg)).days > months * 30.5
    except ValueError:
        return True


def _private_candidates(bv, bulkfile: str | None, exclude: set[str]) -> list[dict]:
    """Candidates from a freshly downloaded bulk file, saved as a small CSV that is
    committed; without a bulk file the saved CSV is used (as on GitHub)."""
    from pathlib import Path

    if bulkfile and Path(bulkfile).exists():
        rows = [
            r
            for r in bv.private_candidates(bv.read_bulkfile(Path(bulkfile)), exclude)
            if _old_enough(r)
        ]
        keep = ("org_nr", "name", "registered", "proceedings", "priority")
        pl.DataFrame(
            [{k: r.get(k) for k in keep} for r in rows],
            schema={k: pl.Int64 if k == "priority" else pl.Utf8 for k in keep},
        ).write_parquet(PRIVATE_CANDIDATES, compression="zstd")
        return rows
    if PRIVATE_CANDIDATES.exists():
        df = pl.read_parquet(PRIVATE_CANDIDATES)
        return [r for r in df.to_dicts() if r["org_nr"] not in exclude and _old_enough(r)]
    return []


class _Ledger:
    """Which private companies have been checked, when, and with what result.
    Committed with the snapshot so coverage grows run by run on GitHub."""

    def __init__(self, today: date):
        self.today = today
        self.rows: dict[str, dict] = {}
        if PRIVATE_LEDGER.exists():
            for r in pl.read_csv(PRIVATE_LEDGER, infer_schema_length=0).to_dicts():
                self.rows[r["org_nr"]] = r

    def kept(self, org: str) -> bool:
        return (self.rows.get(org) or {}).get("status") == "kept"

    def skip(self, org: str) -> bool:
        r = self.rows.get(org)
        if not r or r["status"] not in RECHECK_DAYS:
            return False
        return (self.today - date.fromisoformat(r["checked"])).days < RECHECK_DAYS[r["status"]]

    def mark(self, org: str, status: str) -> None:
        self.rows[org] = {"org_nr": org, "checked": self.today.isoformat(), "status": status}

    def save(self) -> None:
        if self.rows:
            pl.DataFrame(list(self.rows.values())).sort("org_nr").write_csv(PRIVATE_LEDGER)


def build_private(
    f: Fetcher,
    company: pl.DataFrame,
    bulkfile: str | None,
    as_of: date,
    max_new: int | None = None,
) -> dict[str, pl.DataFrame | dict | None]:
    """SNI codes for known issuers, and the private-AB universe from Bolagsverket.

    Needs API credentials. The bulk file is optional: without it only the SNI
    check for bond issuers runs.
    """
    import json as _json

    from headroom.config import CACHE_DIR
    from headroom.config import weights as load_weights
    from headroom.sources import bolagsverket as bv

    cfg = load_weights().get("private_universe", {})
    api = bv.Bolagsverket(f)
    cache = CACHE_DIR / "bolagsverket"
    cache.mkdir(parents=True, exist_ok=True)

    def cached(kind: str, org: str, fn):
        p = cache / f"{kind}_{org}.json"
        if p.exists() and (as_of - date.fromtimestamp(p.stat().st_mtime)).days < 30:
            return _json.loads(p.read_text())
        val = fn(org)
        p.write_text(_json.dumps(val, ensure_ascii=False, default=str))
        return val

    sni_rows: list[dict] = []
    evs: list[dict] = []
    for org in company["org_nr"].to_list():
        try:
            rec = cached("org", org, api.organisation)
        except Exception as e:
            log.warning("bolagsverket %s: %s", org, e)
            continue
        codes = bv.sni_codes(rec)
        proc = bv.proceedings(rec)
        for word, typ in bv.PROCEEDING_TYPES if proc else ():
            if word in proc:
                evs.append(
                    {
                        "org_nr": org,
                        "date": as_of,
                        "type": typ,
                        "severity": 3,
                        "title": f"Registered proceeding at Bolagsverket: {word}",
                        "source_url": f"{bv.BASE}/organisationer",
                        "source_name": "Bolagsverket",
                    }
                )
        county, city = geo.from_address(*bv.address(rec))
        sni_rows.append(
            {
                "org_nr": org,
                "sni": ", ".join(codes) or None,
                "sni_real_estate": bv.is_real_estate(codes),
                "county": county,
                "city": city,
            }
        )

    companies, fins = [], []
    stats = {"sni_checked": len(sni_rows), "bulk_candidates": 0, "private_kept": 0}
    rows = _private_candidates(bv, bulkfile, set(company["org_nr"]))
    found = load_found()
    stats["bulk_candidates"] = len(rows)
    ledger = _Ledger(as_of)
    fresh = 0
    limit = int(cfg.get("max_lookups_per_run", 400)) if max_new is None else max_new
    deadline = time.monotonic() + 60 * float(cfg.get("max_minutes_per_run", 240))
    for r in rows:
        if fresh >= limit or time.monotonic() > deadline:
            break
        org = r["org_nr"]
        if ledger.skip(org) or (ledger.kept(org) and org in found):
            continue
        fresh += 1
        if fresh % 500 == 0:  # keep progress if the run is stopped early
            ledger.save()
            save_found(found)
            log.info("private scan: %d checked this run", fresh)
        try:
            rec = cached("org", org, api.organisation)
            codes = bv.sni_codes(rec)
            if codes and not bv.is_real_estate(codes):
                ledger.mark(org, "not_real_estate")
                continue
            docs = cached("docs", org, api.documents)
            doc = bv.latest_annual_report(docs)
            if not doc:
                ledger.mark(org, "no_digital_report")
                continue
            zpath = cache / f"doc_{doc['dokumentId']}.zip"
            if not zpath.exists():
                zpath.write_bytes(api.document(doc["dokumentId"]))
            xhtml = bv.ixbrl_from_zip(zpath.read_bytes())
            if not xhtml:
                ledger.mark(org, "no_ixbrl")
                continue
            a = bv.annual_figures(bv.parse_ixbrl(xhtml))
        except bv.NoCredentials:
            raise
        except Exception as e:
            log.warning("private %s: %s", org, e)
            continue
        pv = a.get("property_value")
        if not pv or pv < float(cfg.get("min_property_value_sek_m", 20)):
            ledger.mark(org, "below_threshold")
            continue
        ledger.mark(org, "kept")
        src = f"{bv.BASE}/dokument/{doc['dokumentId']}"
        ev: list[dict] = []
        if bv.is_late(a.get("period_end"), as_of):
            ev.append(
                {
                    "org_nr": org,
                    "date": as_of,
                    "type": "late_annual_report",
                    "severity": 2,
                    "title": f"No annual report filed for the year after {a['period_end']}",
                    "source_url": f"{bv.BASE}/dokumentlista",
                    "source_name": "Bolagsverket",
                }
            )
        proc = bv.proceedings(rec) or (r.get("proceedings") or "").lower()
        for word, typ in bv.PROCEEDING_TYPES:
            if word in proc:
                ev.append(
                    {
                        "org_nr": org,
                        "date": as_of,
                        "type": typ,
                        "severity": 3,
                        "title": f"Registered proceeding at Bolagsverket: {word}",
                        "source_url": f"{bv.BASE}/organisationer",
                        "source_name": "Bolagsverket",
                    }
                )
        county, city = geo.from_address(*bv.address(rec))
        found[org] = {
            "company": {
                "org_nr": org,
                "county": county,
                "city": city,
                "lei": None,
                "name": r["name"],
                "tier": "private",
                "listed_ticker": None,
                "sni": ", ".join(codes) or None,
                "segment_mix": None,
                "region_mix": None,
                "size": pv,
                "universe_rule": "bolagsverket SNI 68",
                "source_url": f"{bv.BASE}/organisationer",
                "as_of": as_of,
            },
            "financials": bv.to_financials(org, a, src),
            "events": ev,
        }
    if fresh:
        ledger.save()
        save_found(found)
    # Kept private companies live in data/private_found.jsonl and are merged by the app.
    stats["private_kept"] = len(found)
    stats["private_new_lookups"] = fresh
    return {
        "sni": pl.DataFrame(sni_rows) if sni_rows else None,
        "company": pl.DataFrame(companies) if companies else None,
        "financials": pl.DataFrame(fins, infer_schema_length=None) if fins else None,
        "event": pl.DataFrame(evs) if evs else None,
        "meta": stats,
    }
