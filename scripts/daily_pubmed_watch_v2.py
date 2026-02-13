#!/usr/bin/env python3
"""
Daily PubMed Watch — PI-level Evolution / Viromics / Metagenomics radar

What this does
- Pulls PubMed hits for the last N days across PI-aligned THEMES (virome surveillance, urban microbiome,
  selection/codon models, phylodynamics, recombination, pipelines, atlases, ML-evo lane, etc.)
- Builds docs/latest.json, docs/latest.md, docs/index.html (static; GitHub Actions + Pages friendly)
- Ranks *within each theme* using a lightweight PI-style relevance score (not just PubDate)

Usage:
  python daily_pubmed_watch.py --days 1 --max 12
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


# =========================
# v2: Theme taxonomy
# =========================

NEGATIVE_COMMON: List[str] = [
    # Keep these conservative; PubMed "term" syntax can vary by endpoint/tools.
    # You can add more as you notice noise in your feed.
    "case report",
    "randomized",
    "psychology",
]

BOOSTERS_CORE: List[str] = [
    '"computational evolutionary biology"',
    '"molecular evolution"',
    "phylogenomics",
    "phylodynamics",
    "HyPhy",
    '"codon model"',
    '"dN/dS"',
    "BUSTED",
    "FEL",
    "MEME",
    "FUBAR",
    '"selection analysis"',
    "recombination",
    "reassortment",
    '"built environment"',
    '"urban microbiome"',
    "MetaSUB",
    "metagenomics",
    "virome",
    "viromics",
    '"viral metagenomics"',
    "biosurveillance",
    '"pathogen surveillance"',
    '"pandemic preparedness"',
]

BOOSTERS_METHODS: List[str] = [
    "benchmark*",
    "workflow",
    "pipeline",
    "Snakemake",
    "Nextflow",
    "WDL",
    "CWL",
    "HPC",
    "container*",
    "Docker",
    "Singularity",
    "Apptainer",
]

BOOSTERS_ML: List[str] = [
    '"machine learning"',
    '"deep learning"',
    "transformer",
    '"foundation model"',
    '"protein language model"',
    "ESM",
    "AlphaFold",
]


@dataclass
class Theme:
    name: str
    core_queries: List[str]
    boosters: List[str] = field(default_factory=list)
    negatives: List[str] = field(default_factory=list)
    priority: float = 1.0  # used in scoring


THEMES: Dict[str, Theme] = {
    # Aim 1: evolutionary inference
    "Selection & codon models": Theme(
        name="Selection & codon models",
        core_queries=[
            '("dN/dS" OR "codon model" OR HyPhy OR FEL OR MEME OR FUBAR OR BUSTED OR "branch-site") '
            'AND (selection OR evolution OR adaptive OR constraint)',
            '("episodic selection" OR "positive selection" OR "diversifying selection") AND (virus OR pathogen)',
        ],
        boosters=BOOSTERS_CORE + BOOSTERS_METHODS,
        negatives=NEGATIVE_COMMON,
        priority=1.35,
    ),
    "Recombination & mosaicism": Theme(
        name="Recombination & mosaicism",
        core_queries=[
            '(recombination OR "mosaic genome" OR breakpoint OR "gene conversion") AND (virus OR virome OR pathogen)',
            '(RDP OR GARD OR "recombination detection") AND (sequence OR alignment)',
        ],
        boosters=BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.20,
    ),
    "Phylodynamics & transmission": Theme(
        name="Phylodynamics & transmission",
        core_queries=[
            '(phylodynamic* OR "time-resolved phylogeny" OR BEAST OR "birth-death" OR coalescent) AND (virus OR pathogen)',
            '("genomic epidemiology" OR "phylodynamic inference") AND (outbreak OR transmission)',
        ],
        boosters=BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.20,
    ),
    # Aim 2: urban/wastewater surveillance (MetaSUB/UrbanScope aligned)
    "Urban / built environment virome": Theme(
        name="Urban / built environment virome",
        core_queries=[
            '(virome OR "viral metagenomics" OR viromics) AND ("built environment" OR urban OR subway OR transit OR surface)',
            '("urban microbiome" OR MetaSUB) AND (virus OR virome OR phage)',
        ],
        boosters=BOOSTERS_CORE + BOOSTERS_METHODS,
        negatives=NEGATIVE_COMMON,
        priority=1.30,
    ),
    "Wastewater / WBE viral surveillance": Theme(
        name="Wastewater / WBE viral surveillance",
        core_queries=[
            '("wastewater surveillance" OR WBE OR sewage) AND (virus OR virome OR pathogen OR SARS-CoV-2)',
            '(wastewater OR sewage) AND ("viral metagenomics" OR viromics) AND (variant OR lineage OR evolution)',
        ],
        boosters=BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.25,
    ),
    "Pandemic preparedness & biosurveillance": Theme(
        name="Pandemic preparedness & biosurveillance",
        core_queries=[
            '("pandemic preparedness" OR biosurveillance OR "pathogen surveillance") AND (genomics OR sequencing)',
            '("early warning" OR "sentinel surveillance") AND (metagenomics OR sequencing)',
        ],
        boosters=BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.15,
    ),
    # Aim 3: atlases + pipelines
    "Atlases, compendia, reference resources": Theme(
        name="Atlases, compendia, reference resources",
        core_queries=[
            '(atlas OR database OR compendium OR "reference catalog" OR resource) AND (virome OR microbiome OR pathogen)',
            '("large-scale" OR global) AND (virome OR microbiome) AND (metadata OR harmonization)',
        ],
        boosters=BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.10,
    ),
    "Metagenomics benchmarking & pipelines": Theme(
        name="Metagenomics benchmarking & pipelines",
        core_queries=[
            "metagenomics AND (benchmark* OR pipeline OR \"best practices\" OR reproducible OR workflow)",
            "(Kraken2 OR MetaPhlAn OR Bracken OR Centrifuge OR Kaiju) AND (benchmark* OR evaluation)",
        ],
        boosters=BOOSTERS_METHODS + BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.05,
    ),
    # Optional lane: ML + evolution (kept separate to control noise)
    "ML for evolution & pathogens": Theme(
        name="ML for evolution & pathogens",
        core_queries=[
            '("machine learning" OR "deep learning" OR transformer) AND (virus OR pathogen OR evolution OR phylogeny)',
            '("protein language model" OR ESM) AND (mutation OR evolution OR fitness)',
        ],
        boosters=BOOSTERS_ML + BOOSTERS_CORE,
        negatives=NEGATIVE_COMMON,
        priority=1.00,
    ),
}


def build_query(theme: Theme, booster_strength: int = 6, negative_strength: int = 6) -> str:
    """
    Build a PubMed ESearch 'term' string:
      (core1 OR core2 OR ...) AND (boosters...) NOT (negatives...)
    Keep booster/negative lists short to avoid overly long queries.
    """
    core = "(" + " OR ".join(f"({q})" for q in theme.core_queries) + ")"

    boosters = theme.boosters[:booster_strength]
    boost_block = ""
    if boosters:
        boost_block = " AND (" + " OR ".join(boosters) + ")"

    negatives = theme.negatives[:negative_strength]
    neg_block = ""
    if negatives:
        neg_block = " NOT (" + " OR ".join(negatives) + ")"

    return core + boost_block + neg_block


DEFAULT_QUERIES_V2: Dict[str, str] = {k: build_query(v) for k, v in THEMES.items()}


# =========================
# v2: PI-style scoring
# =========================

SCORING_TERMS: List[Tuple[str, float]] = [
    (r"\bdN/dS\b", 4.0),
    (r"codon model", 4.0),
    (r"\bHyPhy\b", 4.0),
    (r"\bBUSTED\b", 3.5),
    (r"\bFEL\b|\bMEME\b|\bFUBAR\b", 3.0),
    (r"episodic selection|diversifying selection|positive selection", 3.0),
    (r"phylodynamic|time-resolved|BEAST|coalescent", 2.8),
    (r"recombination|reassortment|breakpoint|mosaic", 2.8),
    (r"virome|viromics|viral metagenomics", 2.5),
    (r"built environment|urban microbiome|MetaSUB|subway|transit", 2.5),
    (r"wastewater|WBE|sewage", 2.2),
    (r"biosurveillance|pandemic preparedness|early warning|sentinel surveillance", 2.0),
    (r"benchmark|evaluation|best practices", 1.8),
    (r"Snakemake|Nextflow|CWL|WDL|reproducible|container|Docker|Singularity|Apptainer", 1.6),
    (r"transformer|foundation model|protein language model|ESM|AlphaFold", 1.2),
]

VENUE_BOOST: List[Tuple[str, float]] = [
    (r"molecular biology and evolution|mol\s*biol\s*evol", 1.8),
    (r"nature microbiology", 1.8),
    (r"genome biology", 1.6),
    (r"\bpnas\b", 1.4),
    (r"\belife\b", 1.2),
    (r"biorxiv|medrxiv", 1.0),
]


def _regex_score(text: str, patterns: List[Tuple[str, float]]) -> float:
    s = 0.0
    for pat, w in patterns:
        if re.search(pat, text, flags=re.I):
            s += w
    return s


def recency_boost(published_date: Optional[dt.datetime], half_life_days: float = 120.0) -> float:
    """
    Exponential decay boost: 1.0 at age 0, 0.5 at half-life.
    """
    if not published_date:
        return 0.0
    now = dt.datetime.now(dt.timezone.utc)
    if published_date.tzinfo is None:
        published_date = published_date.replace(tzinfo=dt.timezone.utc)
    age_days = max(0.0, (now - published_date).total_seconds() / 86400.0)
    return math.exp(-math.log(2) * age_days / half_life_days)


def score_paper(
    title: str,
    abstract: str,
    theme_key: str,
    published_date: Optional[dt.datetime],
    venue: str,
) -> float:
    text = f"{title}\n{abstract}"
    base = THEMES.get(theme_key, Theme(theme_key, [])).priority
    kw = _regex_score(text, SCORING_TERMS)
    ven = _regex_score(venue, VENUE_BOOST)
    rec = recency_boost(published_date, half_life_days=120.0)
    return base + kw + ven + 1.5 * rec


# =========================
# PubMed E-utilities
# =========================

def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "daily-pubmed-watch/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def esearch(term: str, mindate: str, maxdate: str, retmax: int) -> List[str]:
    params = {
        "db": "pubmed",
        "term": term,
        "retmode": "xml",
        "retmax": str(retmax),
        "sort": "pub+date",
        "mindate": mindate,
        "maxdate": maxdate,
        "datetype": "pdat",
    }
    url = EUTILS + "esearch.fcgi?" + urllib.parse.urlencode(params)
    xml_bytes = http_get(url)
    root = ET.fromstring(xml_bytes)
    return [node.text for node in root.findall(".//IdList/Id") if node.text]


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_pubdate(article: ET.Element) -> Tuple[str, Optional[dt.datetime]]:
    """
    Best-effort parse to a display string and a datetime (UTC).
    PubMed XML is messy across records; we try a few common spots.
    """
    # Prefer ArticleDate (often has full Y/M/D)
    y = article.findtext(".//ArticleDate/Year")
    m = article.findtext(".//ArticleDate/Month")
    d = article.findtext(".//ArticleDate/Day")
    if y and m and d:
        try:
            dd = dt.datetime(int(y), int(m), int(d), tzinfo=dt.timezone.utc)
            return dd.date().isoformat(), dd
        except Exception:
            pass

    # Then PubDate
    y = article.findtext(".//JournalIssue/PubDate/Year") or article.findtext(".//PubDate/Year")
    m = article.findtext(".//JournalIssue/PubDate/Month") or article.findtext(".//PubDate/Month")
    d = article.findtext(".//JournalIssue/PubDate/Day") or article.findtext(".//PubDate/Day")
    if y:
        try:
            yy = int(y)
            mm = 1
            if m:
                m_clean = m.strip()
                if m_clean.isdigit():
                    mm = int(m_clean)
                else:
                    mm = _MONTHS.get(m_clean[:3].lower(), 1)
            dd_i = int(d) if (d and d.strip().isdigit()) else 1
            dd = dt.datetime(yy, mm, dd_i, tzinfo=dt.timezone.utc)
            disp = f"{yy:04d}-{mm:02d}" + (f"-{dd_i:02d}" if d else "")
            return disp, dd
        except Exception:
            pass

    # Then MedlineDate (often "2024 Jan-Feb" or "2023")
    med = (article.findtext(".//JournalIssue/PubDate/MedlineDate") or "").strip()
    if med:
        m = re.search(r"(\d{4})", med)
        if m:
            yy = int(m.group(1))
            dd = dt.datetime(yy, 1, 1, tzinfo=dt.timezone.utc)
            return str(yy), dd
        return med, None

    return "", None


def efetch_details(pmids: List[str], theme_key: str) -> List[Dict[str, Any]]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    url = EUTILS + "efetch.fcgi?" + urllib.parse.urlencode(params)
    xml_bytes = http_get(url)
    root = ET.fromstring(xml_bytes)

    items: List[Dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = (article.findtext(".//PMID") or "").strip()
        title = (article.findtext(".//ArticleTitle") or "").strip()

        # Abstract can have multiple AbstractText nodes (and labels)
        abs_nodes = article.findall(".//Abstract/AbstractText")
        abstract = " ".join([(n.text or "").strip() for n in abs_nodes if (n.text or "").strip()]).strip()

        journal = (article.findtext(".//Journal/Title") or "").strip()

        pubdate_str, pubdate_dt = _parse_pubdate(article)

        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""

        authors = []
        for a in article.findall(".//AuthorList/Author")[:6]:
            last = (a.findtext("LastName") or "").strip()
            fore = (a.findtext("ForeName") or "").strip()
            if last and fore:
                authors.append(f"{fore} {last}")
            elif last:
                authors.append(last)
        author_str = ", ".join(authors)

        snippet = abstract[:260] + ("…" if len(abstract) > 260 else "")

        score = score_paper(
            title=title or "",
            abstract=abstract or "",
            theme_key=theme_key,
            published_date=pubdate_dt,
            venue=journal or "",
        )

        items.append(
            {
                "pmid": pmid,
                "title": title,
                "authors": author_str,
                "journal": journal,
                "pubdate": pubdate_str,
                "pubdate_utc": pubdate_dt.isoformat() if pubdate_dt else "",
                "link": link,
                "abstract_snippet": snippet,
                "score": round(float(score), 3),
                "theme": theme_key,
            }
        )
    return items


def rank_and_trim(items: List[Dict[str, Any]], max_items: int) -> List[Dict[str, Any]]:
    # Sort by PI score desc; break ties by pubdate_utc desc (if present)
    def key(it: Dict[str, Any]) -> Tuple[float, str]:
        return (float(it.get("score", 0.0)), it.get("pubdate_utc", ""))

    items_sorted = sorted(items, key=key, reverse=True)
    return items_sorted[:max_items]


# =========================
# Outputs
# =========================

def write_outputs(outdir_docs: str, payload: Dict[str, Any]) -> None:
    # JSON
    json_path = f"{outdir_docs}/latest.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Markdown
    md_path = f"{outdir_docs}/latest.md"
    lines: List[str] = []
    lines.append("# Daily PubMed Watch (evo • virome • metagenomics)\n")
    lines.append(f"**Updated:** {payload['generated_at_local']}  ")
    lines.append(f"**Window:** last {payload['days']} day(s)  ")
    lines.append(f"**Ranking:** Score (theme priority + keyword signals + venue + recency)\n")

    for block in payload["sections"]:
        lines.append(f"\n## {block['label']} — {block['count']} result(s)\n")
        if not block["items"]:
            lines.append("_No new items in this window._")
            continue
        for it in block["items"]:
            title = it["title"] or "(no title)"
            score = it.get("score", 0.0)
            lines.append(f"- **[{title}]({it['link']})**  ")
            meta = " · ".join([x for x in [it["authors"], it["journal"], it["pubdate"]] if x])
            if meta:
                lines.append(f"  {meta}  ")
            lines.append(f"  _PI score:_ `{score}`  ")
            if it["abstract_snippet"]:
                lines.append(f"  _{it['abstract_snippet']}_")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


    # HTML page (self-contained; GitHub Pages friendly)
    html_path = f"{outdir_docs}/index.html"
    html_body: List[str] = []
    html_body.append("<!doctype html><html><head><meta charset='utf-8'/>")
    html_body.append("<meta name='viewport' content='width=device-width,initial-scale=1'/>")
    html_body.append("<link rel='icon' type='image/png' href='https://raw.githubusercontent.com/aglucaci/litscan/refs/heads/main/logo/liscan_logo.png'/>")
    html_body.append("<link rel='shortcut icon' type='image/png' href='https://raw.githubusercontent.com/aglucaci/litscan/refs/heads/main/logo/liscan_logo.png'/>")
    html_body.append("<link rel='apple-touch-icon' href='https://raw.githubusercontent.com/aglucaci/litscan/refs/heads/main/logo/liscan_logo.png'/>")
    html_body.append("<title>LitScan</title>")
    html_body.append("""
<style>
  :root{
    --fg:#1f2328; --muted:#57606a; --border:#d0d7de; --card:#ffffff; --chip:#f6f8fa;
    --link:#0969da; --shadow:0 8px 24px rgba(140,149,159,.20);
  }
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:1120px;margin:36px auto;padding:0 16px;line-height:1.55;color:var(--fg);background:#fff}
  a{color:var(--link);text-decoration:none} a:hover{text-decoration:underline}
  .muted{color:var(--muted)}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}
  .chip{display:inline-flex;align-items:center;gap:8px;background:var(--chip);border:1px solid var(--border);border-radius:999px;padding:4px 10px;font-size:.85em;color:#24292f}
  .chip b{font-weight:650}
  .top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:14px;margin-bottom:18px
  .top-header{padding:22px 0 10px;border-bottom:1px solid var(--border);margin-bottom:18px}
  .brand{display:flex;align-items:center;gap:16px}
  .logo{height:64px;width:auto;border-radius:10px;filter:drop-shadow(0 0 10px rgba(46,168,255,.25))}
  .brand-text{display:flex;flex-direction:column}
}
  .brand h1{margin:0;font-size:28px;letter-spacing:.2px}
  .brand p{margin:6px 0 0;max-width:760px}
  .meta{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
  .grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;margin:18px 0 20px}
  .stat{grid-column:span 3;border:1px solid var(--border);border-radius:16px;background:var(--card);padding:12px 14px;box-shadow:var(--shadow)}
  .stat .k{font-size:.78em;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
  .stat .v{margin-top:4px;font-size:1.1em;font-weight:700}
  .stat .s{margin-top:6px;font-size:.92em;color:var(--muted)}
  @media (max-width: 980px){ .stat{grid-column:span 6} }
  @media (max-width: 560px){ .stat{grid-column:span 12} }

  .nav{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 22px}
  .nav a{border:1px solid var(--border);background:var(--chip);border-radius:12px;padding:6px 10px;font-size:.9em}

  .block{border:1px solid var(--border);border-radius:18px;background:var(--card);padding:14px 14px 10px;margin:14px 0;box-shadow:var(--shadow)}
  .blockhead{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
  .blocktitle{display:flex;align-items:center;gap:10px}
  .blocktitle h2{margin:0;font-size:18px}
  .count{background:var(--chip);border:1px solid var(--border);border-radius:999px;padding:3px 10px;font-size:.85em;color:#24292f}
  details{margin-top:6px}
  details summary{cursor:pointer;color:var(--muted)}
  .card{border:1px solid var(--border);border-radius:14px;padding:12px 12px;margin:10px 0;background:#fff}
  .card .t{font-weight:650}
  .card .meta{margin-top:6px;font-size:.92em;color:var(--muted)}
  .card .abs{margin-top:9px;color:var(--muted);font-size:.95em}
  .score{margin-left:8px}
  .footer{margin:26px 0 10px;color:var(--muted);font-size:.95em}
</style>
""")
    html_body.append("</head><body>")

    # Top summary
    total_hits = sum(int(s.get("count", 0) or 0) for s in payload.get("sections", []))
    theme_count = len(payload.get("sections", []))
    html_body.append("<header class='top-header'>")
    html_body.append("<div class='top'>")
    html_body.append("<div class='brand'>")
    html_body.append("<img src='https://raw.githubusercontent.com/aglucaci/litscan/refs/heads/main/logo/liscan_logo.png' alt='LitScan Logo' class='logo'/>")
    html_body.append("<div class='brand-text'>")
    html_body.append("<h1>LitScan</h1>")
    html_body.append(
        "<p class='muted'>Adaptive literature radar with half-life weighting — evo/virology/metagenomics/cancer evolution, re-ranked per theme.</p>"
    )
    html_body.append("</div>")
    html_body.append("</div>")
    html_body.append("<div class='meta'>")
    html_body.append(f"<span class='chip'><b>Updated</b> {html.escape(payload['generated_at_local'])}</span>")
    html_body.append(f"<span class='chip'><b>Window</b> last {int(payload['days'])} day(s)</span>")
    html_body.append(f"<span class='chip'><b>Themes</b> {theme_count}</span>")
    html_body.append(f"<span class='chip'><b>Total hits</b> {total_hits}</span>")
    html_body.append("</div>")
    html_body.append("</div>")

    html_body.append("<div class='grid'>")
    html_body.append("<div class='stat'><div class='k'>Downloads</div><div class='v'>Artifacts</div>"
                     "<div class='s'><a href='latest.json'>latest.json</a> · <a href='latest.md'>latest.md</a></div></div>")
    html_body.append("<div class='stat'><div class='k'>Ranking</div><div class='v'>Relevance score</div>"
                     "<div class='s'>Theme priority + keyword signals + venue boost + recency decay</div></div>")
    html_body.append("<div class='stat'><div class='k'>Scope</div><div class='v'>PubMed</div>"
                     "<div class='s'>Fetched via NCBI E-utilities; sorted by PubDate then re-ranked per theme</div></div>")
    html_body.append("<div class='stat'><div class='k'>Tip</div><div class='v'>Tune noise</div>"
                     "<div class='s'>Use <span class='mono'>--boosters</span>/<span class='mono'>--negatives</span> to widen/narrow</div></div>")
    html_body.append("</div>")

    # Quick nav
    html_body.append("<div class='nav'>")
    for idx, block in enumerate(payload.get("sections", []), start=1):
        anchor = f"sec-{idx}"
        label = html.escape(block.get("label", "Section"))
        cnt = int(block.get("count", 0) or 0)
        html_body.append(f"<a href='#{anchor}'>{label} <span class='mono'>({cnt})</span></a>")
    html_body.append("</div>")
    html_body.append("</header>")

    # Theme blocks
    for idx, block in enumerate(payload.get("sections", []), start=1):
        anchor = f"sec-{idx}"
        label = html.escape(block.get("label", "Section"))
        cnt = int(block.get("count", 0) or 0)
        q = html.escape(block.get("query", ""))

        html_body.append(f"<section class='block' id='{anchor}'>")
        html_body.append("<div class='blockhead'>")
        html_body.append(f"<div class='blocktitle'><h2>{label}</h2><span class='count'>{cnt} result(s)</span></div>")
        html_body.append("</div>")

        if q:
            html_body.append("<details>")
            html_body.append("<summary>Show query</summary>")
            html_body.append(f"<div class='muted mono' style='margin-top:8px;white-space:pre-wrap'>{q}</div>")
            html_body.append("</details>")

        if not block.get("items"):
            html_body.append("<p class='muted' style='margin:10px 0 6px'>No new items in this window.</p>")
            html_body.append("</section>")
            continue

        for it in block.get("items", []):
            title = html.escape(it.get("title") or "(no title)")
            link = html.escape(it.get("link") or "#")
            meta_parts = [it.get("authors", ""), it.get("journal", ""), it.get("pubdate", "")]
            meta = " · ".join([html.escape(m) for m in meta_parts if m])
            snippet = html.escape(it.get("abstract_snippet", ""))
            score = html.escape(str(it.get("score", 0.0)))

            html_body.append("<div class='card'>")
            html_body.append(
                f"<div class='t'><a href='{link}' target='_blank' rel='noopener'>{title}</a>"
                f"<span class='chip score'><b>score</b> <span class='mono'>{score}</span></span></div>"
            )
            if meta:
                html_body.append(f"<div class='meta'>{meta}</div>")
            if snippet:
                html_body.append(f"<div class='abs'>{snippet}</div>")
            html_body.append("</div>")

        html_body.append("</section>")

    html_body.append("<div class='footer'>Generated automatically from PubMed via NCBI E-utilities. For informational use only.</div>")
    html_body.append("</body></html>")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write("".join(html_body))



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="Lookback window in days (default: 1)")
    ap.add_argument("--max", type=int, default=12, help="Max items per section (default: 12)")
    ap.add_argument("--docs-dir", default="docs", help="Docs output directory (default: docs)")
    ap.add_argument(
        "--boosters",
        type=int,
        default=6,
        help="How many booster terms to include per theme query (default: 6).",
    )
    ap.add_argument(
        "--negatives",
        type=int,
        default=6,
        help="How many negative terms to include per theme query (default: 6).",
    )
    args = ap.parse_args()

    # Rebuild queries with CLI knobs (so you can quickly tune noise in Actions)
    queries = {k: build_query(v, booster_strength=args.boosters, negative_strength=args.negatives) for k, v in THEMES.items()}

    # Date window (UTC for PubMed pdat filtering)
    end = dt.datetime.utcnow().date()
    start = end - dt.timedelta(days=max(args.days, 1))
    mindate = start.strftime("%Y/%m/%d")
    maxdate = end.strftime("%Y/%m/%d")

    generated_at_local = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections: List[Dict[str, Any]] = []
    for label, term in queries.items():
        try:
            pmids = esearch(term, mindate=mindate, maxdate=maxdate, retmax=args.max)
            time.sleep(0.34)  # be polite to NCBI
            details = efetch_details(pmids, theme_key=label)
            details = rank_and_trim(details, args.max)
            sections.append({"label": label, "query": term, "count": len(details), "items": details})
            time.sleep(0.34)
        except Exception as e:
            sections.append({"label": label, "query": term, "count": 0, "items": [], "error": str(e)})

    payload: Dict[str, Any] = {
        "generated_at_local": generated_at_local,
        "generated_at_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "days": args.days,
        "window_utc": {"mindate": mindate, "maxdate": maxdate},
        "ranking": "pi_score(theme_priority + keyword_signals + venue + recency)",
        "themes": {k: {"priority": v.priority} for k, v in THEMES.items()},
        "sections": sections,
    }

    os.makedirs(args.docs_dir, exist_ok=True)
    write_outputs(args.docs_dir, payload)
    print(f"Wrote {args.docs_dir}/index.html, latest.json, latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
