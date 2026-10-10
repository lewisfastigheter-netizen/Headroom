"""Bolagsverket "Värdefulla datamängder": company data and digitally filed annual reports.

API (OAuth2 client credentials, free registration at Bolagsverket's API portal):
  token:  POST https://portal.api.bolagsverket.se/oauth2/token
  base:   https://gw.api.bolagsverket.se/vardefulla-datamangder/v1
  POST /organisationer   {"identitetsbeteckning": "5565203186"}  company data incl. SNI codes
  POST /dokumentlista    {"identitetsbeteckning": "5565203186"}  filed annual reports
  GET  /dokument/{id}    zip with the iXBRL annual report
Endpoints and the token URL were confirmed to exist (401 without credentials,
404 for unknown paths) on 9 Oct 2026. Response field names are read defensively,
because they could not be checked without credentials.

Bulk file: Bolagsverket also publishes a free bulk file of all registered
companies. Its website sits behind bot protection, so the pipeline never
scrapes it: download the file in a browser and point `BOLAGSVERKET_BULKFILE`
(or `--bulkfile`) at it.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from headroom.config import settings
from headroom.http import HOST_INTERVAL, Fetcher
from headroom.model import valuation
from headroom.schemas import normalise_org_nr

log = logging.getLogger(__name__)

TOKEN_URL = "https://portal.api.bolagsverket.se/oauth2/token"
BASE = "https://gw.api.bolagsverket.se/vardefulla-datamangder/v1"
SCOPE = "vardefulla-datamangder:read vardefulla-datamangder:ping"


class NoCredentials(RuntimeError):
    pass


class BadCredentials(NoCredentials):
    """The token endpoint rejected the client id and secret (wrong or test-environment keys)."""


@dataclass
class Token:
    value: str
    expires: float


class Bolagsverket:
    def __init__(self, f: Fetcher):
        s = settings()
        if not (s.bolagsverket_client_id and s.bolagsverket_client_secret):
            raise NoCredentials("Set BOLAGSVERKET_CLIENT_ID and BOLAGSVERKET_CLIENT_SECRET in .env")
        self.f = f
        self.cid, self.secret = s.bolagsverket_client_id, s.bolagsverket_client_secret
        self._token: Token | None = None

    def token(self) -> str:
        if self._token and self._token.expires > time.time() + 60:
            return self._token.value
        r = self.f.client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": SCOPE},
            auth=(self.cid, self.secret),
            timeout=30,
        )
        if r.status_code in (400, 401, 403):
            raise BadCredentials(
                f"Bolagsverket rejected the API credentials ({r.status_code}). Check that "
                "BOLAGSVERKET_CLIENT_ID and BOLAGSVERKET_CLIENT_SECRET are the production keys "
                "(token endpoint portal.api.bolagsverket.se), not the test-environment ones."
            )
        r.raise_for_status()
        j = r.json()
        self._token = Token(j["access_token"], time.time() + float(j.get("expires_in", 3000)))
        return self._token.value

    def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """One API call, spaced by the host interval. On 429 (rate limit) wait and retry,
        and slow down for the rest of the run: the gateway gives no rate-limit headers."""
        host = "gw.api.bolagsverket.se"
        for wait in (10, 30, 60, 120, None):
            self.f._wait(host)
            r = self.f.client.request(
                method, url, headers={"Authorization": f"Bearer {self.token()}"}, **kw
            )
            if r.status_code != 429 or wait is None:
                return r
            HOST_INTERVAL[host] = min(HOST_INTERVAL.get(host, 1.5) * 1.5, 6.0)
            log.info(
                "Bolagsverket rate limit: waiting %ss, interval now %.1fs",
                wait,
                HOST_INTERVAL[host],
            )
            time.sleep(wait)
        return r

    def _post(self, path: str, org_nr: str) -> Any:
        r = self._send(
            "POST",
            f"{BASE}{path}",
            json={"identitetsbeteckning": org_nr.replace("-", "")},
            timeout=60,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def organisation(self, org_nr: str) -> dict | None:
        return self._post("/organisationer", org_nr)

    def documents(self, org_nr: str) -> list[dict]:
        j = self._post("/dokumentlista", org_nr) or {}
        return list(_find_lists(j, "dokumentId"))

    def document(self, doc_id: str) -> bytes:
        r = self._send("GET", f"{BASE}/dokument/{doc_id}", timeout=120)
        r.raise_for_status()
        return r.content


def _find_lists(obj: Any, key: str) -> Iterator[dict]:
    """Yield every dict anywhere in a JSON tree that has `key`."""
    if isinstance(obj, dict):
        if key in obj:
            yield obj
        for v in obj.values():
            yield from _find_lists(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_lists(v, key)


def sni_codes(org: dict | None) -> list[str]:
    """SNI codes from the industry part of the record (naringsgrenOrganisation.sni).

    Only values under a key mentioning 'sni' or 'naringsgren' count, so other
    codes in the record (legal form '49', for instance) are not mistaken for SNI.
    Blank and unknown ('00000') codes are dropped.
    """
    out: list[str] = []

    def walk(o: Any, in_sni: bool = False) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                kl = k.lower()
                walk(v, in_sni or "sni" in kl or "naringsgren" in kl)
        elif isinstance(o, list):
            for v in o:
                walk(v, in_sni)
        elif in_sni and isinstance(o, str) and re.fullmatch(r"\d{2}\.?\d{0,3}", o.strip()):
            code = o.strip().replace(".", "")
            if code.strip("0"):
                out.append(f"{code[:2]}.{code[2:]}" if len(code) > 2 else code)

    walk(org)
    return list(dict.fromkeys(out))


def proceedings(org: dict | None) -> str | None:
    """Ongoing liquidation, bankruptcy or reconstruction registered for the company, as text."""
    if not org:
        return None
    items = org.get("organisationer") if isinstance(org, dict) else None
    rec = items[0] if items else org
    val = (
        rec.get("pagaendeAvvecklingsEllerOmstruktureringsforfarande")
        if isinstance(rec, dict)
        else None
    )
    if not val:
        return None
    return json.dumps(val, ensure_ascii=False).lower()


def address(org: dict | None) -> tuple[str | None, str | None]:
    """(postal town, postcode) of the registered address."""
    if not org:
        return None, None
    items = org.get("organisationer") if isinstance(org, dict) else None
    rec = items[0] if items else org
    pa = ((rec or {}).get("postadressOrganisation") or {}).get("postadress") or {}
    return pa.get("postort"), pa.get("postnummer")


PROCEEDING_TYPES = (
    ("konkurs", "bankruptcy_in_group"),
    ("rekonstruktion", "reconstruction"),
    ("likvidation", "liquidation"),
)


def is_real_estate(snis: list[str]) -> bool:
    return any(s.startswith("68") for s in snis)


# --------------------------------------------------------------------------- iXBRL

# Concepts in the Swedish annual-report taxonomy (se-gen-base) for K2/K3 reports.
# Each target lists candidates in order of preference (the first one found is used).
# Concept names checked against Bolagsverket's taxonomy lists (taxonomier.se): K2 AB
# 2024-09-12 and K3 AB 2021-10-31. On the balance sheet, debt to credit institutions is
# `OvrigaLangfristigaSkulderKreditinstitut` / `OvrigaKortfristigaSkulderKreditinstitut`;
# `SkulderKreditinstitut*` only exist in K3 notes, which is why reading only those left
# most private companies without debt (and so without LTV) until parser version 2.
IXBRL_MAP: dict[str, list[str]] = {
    "property_value": [
        "ForvaltningsfastigheterVerkligtVarde",
        "Forvaltningsfastigheter",
        "ByggnaderMark",
    ],
    "total_assets": ["Tillgangar"],
    "equity": ["EgetKapital"],
    "cash": ["KassaBankExklRedovisningsmedel", "KassaBank", "LikvidaMedel"],
    # interest-bearing debt, long term
    "debt_credit_long": [
        "OvrigaLangfristigaSkulderKreditinstitut",
        "SkulderKreditinstitutLangfristiga",
        "LangfristigaSkulderKreditinstitut",
    ],
    "overdraft_long": ["CheckrakningskreditLangfristig"],
    "bonds_long": ["Obligationslan"],
    "debt_group_long": ["SkulderKoncernforetagLangfristiga"],
    "debt_owner_long": ["SkulderOvrigaForetagAgarintresseLangfristiga"],
    "debt_associate_long": ["SkulderIntresseforetagGemensamtStyrdaForetagLangfristiga"],
    # interest-bearing debt, due within a year
    "debt_credit_short": [
        "OvrigaKortfristigaSkulderKreditinstitut",
        "SkulderKreditinstitutKortfristiga",
        "KortfristigaSkulderKreditinstitut",
    ],
    "overdraft_short": ["CheckrakningskreditKortfristig"],
    "bonds_short": ["ObligationslanKortfristiga"],
    # used only when none of the above is reported but there is interest expense:
    # small companies often book bank and shareholder loans as "other long-term debt"
    "other_long": ["OvrigaLangfristigaSkulder"],
    "operating_profit": ["Rorelseresultat"],
    "interest_expense": ["RantekostnaderLiknandeResultatposter", "Rantekostnader"],
    "revenue": ["Nettoomsattning"],
    "depreciation": ["AvskrivningarNedskrivningarMateriellaImmateriellaAnlaggningstillgangar"],
    "equity_ratio_pct": ["Soliditet"],
}

DEBT_LONG = (
    "debt_credit_long",
    "overdraft_long",
    "bonds_long",
    "debt_group_long",
    "debt_owner_long",
    "debt_associate_long",
)
DEBT_SHORT = ("debt_credit_short", "overdraft_short", "bonds_short")

# Bump when the mapping changes: kept companies parsed by an older version are re-read
# (from the cached annual report) by the daily job.
PARSER_VERSION = 2


@dataclass
class Fact:
    concept: str
    value: float
    context: str
    period_end: date | None


def parse_ixbrl(xhtml: bytes) -> list[Fact]:
    from lxml import etree

    root = etree.fromstring(xhtml, parser=etree.XMLParser(recover=True, huge_tree=True))
    ns_ix = "http://www.xbrl.org/2013/inlineXBRL"
    ctx_end: dict[str, date | None] = {}
    for c in root.iter("{http://www.xbrl.org/2003/instance}context"):
        end = c.find(".//{http://www.xbrl.org/2003/instance}endDate")
        inst = c.find(".//{http://www.xbrl.org/2003/instance}instant")
        txt = end if end is not None else inst
        try:
            ctx_end[c.get("id")] = date.fromisoformat(txt.text.strip()) if txt is not None else None
        except ValueError:
            ctx_end[c.get("id")] = None
    facts: list[Fact] = []
    for el in root.iter(f"{{{ns_ix}}}nonFraction"):
        name = (el.get("name") or "").split(":")[-1]
        raw = "".join(el.itertext()).strip().replace(" ", "").replace("\xa0", "")
        if not raw or el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
            continue
        try:
            v = (
                float(raw.replace(",", "."))
                if raw.count(",") == 1 and "." not in raw
                else float(raw.replace(",", ""))
            )
        except ValueError:
            continue
        v *= 10 ** int(el.get("scale") or 0)
        if el.get("sign") == "-":
            v = -v
        facts.append(
            Fact(name, v, el.get("contextRef") or "", ctx_end.get(el.get("contextRef") or ""))
        )
    return facts


def ixbrl_from_zip(data: bytes) -> bytes | None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n.lower().endswith((".xhtml", ".html", ".htm")):
                return z.read(n)
    return None


def annual_figures(facts: list[Fact]) -> dict[str, float | date | None]:
    """Latest-period values for the mapped concepts, in SEK millions (ratios as shares)."""
    ends = [f.period_end for f in facts if f.period_end]
    if not ends:
        return {}
    latest = max(ends)
    cur = {f.concept: f.value for f in facts if f.period_end == latest}
    # duration facts end on the same date as the balance-sheet instant
    out: dict[str, float | date | str | None] = {"period_end": latest}
    # K3 companies often disclose the fair value of investment property in a note.
    # Prefer it; any concept naming both property and fair value counts.
    fair = next(
        (
            v
            for k, v in cur.items()
            if re.search(r"fastighet", k, re.I) and re.search(r"verkligtvarde", k, re.I)
        ),
        None,
    )
    for target, names in IXBRL_MAP.items():
        val = next((cur[n] for n in names if n in cur), None)
        if val is None:
            out[target] = None
        elif target == "equity_ratio_pct":
            out[target] = val / 100 if val > 1 else val
        else:
            out[target] = val / 1e6
    if fair is not None:
        out["property_value"] = fair / 1e6
        out["value_basis"] = valuation.FAIR
    elif out.get("property_value") is not None:
        out["value_basis"] = valuation.BOOK
    return out


def to_financials(org_nr: str, a: dict, source_url: str) -> dict:
    """Map annual-report figures to the financials table (private ABs).

    Interest-bearing debt is debt to credit institutions, overdraft facilities, bonds
    and long-term loans from group, owner and associated companies. The part due within
    a year (short-term credit institution debt, overdraft and bonds) is the refinancing
    need. If none of these is reported but the company pays interest, other long-term
    debt is used instead, at lower confidence. Property is at fair value when the report
    discloses it in a note, otherwise at book value; `value_basis` says which.
    """
    long = sum(a.get(k) or 0 for k in DEBT_LONG)
    short = sum(a.get(k) or 0 for k in DEBT_SHORT)
    debt = long + short
    cash = a.get("cash")
    pv = a.get("property_value")
    ebitda = None
    if a.get("operating_profit") is not None:
        ebitda = a["operating_profit"] + abs(a.get("depreciation") or 0)
    interest = abs(a["interest_expense"]) if a.get("interest_expense") else None
    confidence = 0.95
    if debt <= 0 and interest and (a.get("other_long") or 0) > 0:
        debt = a["other_long"]
        confidence = 0.8
    avg_rate = (interest / debt) if interest and debt > 0 else None
    if avg_rate is not None and avg_rate > 0.2:
        confidence = min(confidence, 0.6)  # interest far above any market rate: debt incomplete
    eq = a.get("equity_ratio_pct")
    if eq is None and a.get("equity") is not None and a.get("total_assets"):
        eq = a["equity"] / a["total_assets"]
    has_debt = debt > 0
    return {
        "org_nr": org_nr,
        "period_end": a.get("period_end"),
        "period_type": "FY",
        "property_value": pv,
        "value_basis": a.get("value_basis"),
        "gross_debt": debt if has_debt else None,
        "net_debt": (debt - (cash or 0)) if has_debt else None,
        "ltv": ((debt - (cash or 0)) / pv) if has_debt and pv else None,
        "icr": (ebitda / interest) if ebitda is not None and interest else None,
        "ebitda": ebitda,
        "interest_expense": interest,
        "avg_rate": avg_rate,
        "fixed_share": None,
        "fixed_period_years": None,
        "equity_ratio": eq,
        "cash": cash,
        "undrawn_facilities": None,
        "debt_due_12m": short if has_debt else None,
        "debt_due_24m": None,
        "source_url": source_url,
        "page": None,
        "confidence": confidence,
    }


# --------------------------------------------------------------------------- bulk file

BULK_COLUMNS = {
    "org_nr": ("organisationsidentitet", "organisationsnummer", "identitetsbeteckning"),
    "name": ("organisationsnamn", "namn", "foretagsnamn"),
    "form": ("organisationsform",),
    "deregistered": ("avregistreringsdatum",),
    "proceedings": ("pagandeavvecklingselleromstruktureringsforfarande", "pagaende_avveckling"),
    "description": ("verksamhetsbeskrivning",),
    "sni": ("sni", "snikod", "snikoder", "sni_kod", "naringsgren", "naringsgrenkod"),
    "registered": ("registreringsdatum",),
}


def read_bulkfile(path: Path) -> Iterator[dict]:
    """Rows from Bolagsverket's bulk file (zip or text, ; or tab separated)."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith((".txt", ".csv")))
            text = io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace")
            yield from _bulk_rows(text)
    else:
        with open(path, encoding="utf-8", errors="replace") as fh:
            yield from _bulk_rows(fh)


def _bulk_rows(fh: io.TextIOBase) -> Iterator[dict]:
    head = fh.readline()
    delim = ";" if head.count(";") >= head.count("\t") else "\t"
    cols = [
        re.sub(
            r"[^a-z_]", "", c.strip().lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
        )
        for c in head.split(delim)
    ]
    idx = {
        k: next((cols.index(a) for a in alts if a in cols), None)
        for k, alts in BULK_COLUMNS.items()
    }
    for parts in csv.reader(fh, delimiter=delim):
        row = {
            k: (parts[i].strip() if i is not None and i < len(parts) else None)
            for k, i in idx.items()
        }
        try:
            row["org_nr"] = normalise_org_nr((row["org_nr"] or "").split("$")[0])
        except ValueError:
            continue
        # Coded fields look like 'Name$FORETAGSNAMN-ORGNAM$1993-03-15' or 'AB-ORGFO';
        # several names are separated by '|'. Keep the first value of each.
        for k in ("name", "form", "proceedings"):
            if row.get(k):
                row[k] = row[k].split("|")[0].split("$")[0].strip()
        if row.get("form"):
            row["form"] = row["form"].removesuffix("-ORGFO")
        if row.get("proceedings"):
            row["proceedings"] = row["proceedings"].removesuffix("-PAAVOF") or None
        yield row


# Wording in names and registered business descriptions (verksamhetsbeskrivning) of
# companies that own property. STRONG phrases almost always mean a property owner;
# WEAK ones also match other businesses and are checked last. Every candidate is
# confirmed against its SNI code (68.x) through the API before anything is read.
# Ownership wording: the company itself owns and lets property.
OWNER_TEXT = re.compile(
    r"(?:äga|förvärva|förvalta|inneha|avyttra)[^.;]{0,40}?(?:fastighet|fast egendom|byggnad|hyreshus)|"
    r"uthyrning av (?:egna )?(?:lokaler|bostäder|lägenheter|fastigheter|kontor|lager|byggnader)|"
    r"hyra ut (?:lokaler|bostäder|lägenheter|fastigheter)|hyresfastighet|hyreshus|"
    r"bostadsfastighet|kommersiella fastigheter|fastighetsbolag|fastighetsägande|tomträtt",
    re.I,
)
# Property mentioned without clear ownership (developers, mixed businesses).
PROPERTY_TEXT = re.compile(r"fastighet|fast egendom|byggrätt|exploatering|projektutveckling", re.I)
# Services to property owners, not owners: brokers, caretakers, cleaning, valuation.
SERVICE_TEXT = re.compile(
    r"förmedling|mäklar|skötsel|städ|fastighetsservice|besiktning|värdering|"
    r"konsult|rådgivning|fastighetsautomation|teknisk förvaltning|ventilation|el-?install",
    re.I,
)
WEAK_TEXT = re.compile(r"bostäder|lägenheter|real estate|properties|property", re.I)
STRONG_TEXT = OWNER_TEXT  # name kept for callers


def _priority(r: dict) -> int | None:
    """0 = SNI 68 in the file, 1 = owner wording, 2 = property wording, 3 = weak wording.
    None = skip (no property wording, or property services only)."""
    sni = re.sub(r"\D", " ", r.get("sni") or "").split()
    if sni:
        return 0 if any(c.startswith("68") for c in sni) else None
    name, desc = r.get("name") or "", r.get("description") or ""
    text = f"{name} {desc}"
    if OWNER_TEXT.search(text):
        return 1
    if SERVICE_TEXT.search(text):
        return None
    if re.search(r"fastighet", name, re.I) or PROPERTY_TEXT.search(desc):
        return 2
    if WEAK_TEXT.search(text):
        return 3
    return None


def private_candidates(rows: Iterator[dict], exclude: set[str]) -> list[dict]:
    """Active aktiebolag that may own property, most likely first.

    If the bulk file carries SNI codes they decide; otherwise the name and the
    registered business description do. The API's SNI check follows either way.
    """
    out = []
    for r in rows:
        if r["org_nr"] in exclude or r.get("deregistered"):
            continue
        form = (r.get("form") or "").upper()
        if form and form not in ("AB", "AKTIEBOLAG"):
            continue
        prio = _priority(r)
        if prio is not None:
            out.append({**r, "priority": prio})
    return sorted(out, key=lambda r: r["priority"])


def latest_annual_report(docs: list[dict]) -> dict | None:
    """The newest digitally filed annual report in a document list."""

    def end(d: dict) -> str:
        return str(
            d.get("rapporteringsperiodTom")
            or d.get("periodTom")
            or d.get("registreringstidpunkt")
            or ""
        )

    reports = [d for d in docs if "rsredovisning" in str(d).lower() or d.get("filformat")]
    return max(reports or docs, key=end, default=None)


def is_late(last_period_end: date | None, today: date) -> bool:
    """Annual reports are due within seven months of the year end."""
    if last_period_end is None:
        return False
    due = last_period_end + timedelta(days=7 * 30 + 15)
    next_end = (
        date(last_period_end.year + 1, last_period_end.month, last_period_end.day)
        if not (last_period_end.month == 2 and last_period_end.day == 29)
        else date(last_period_end.year + 1, 2, 28)
    )
    return today > next_end + (due - last_period_end)


def http_error(e: Exception) -> bool:
    return isinstance(e, httpx.HTTPError)
