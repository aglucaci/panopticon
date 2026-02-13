# LitScan

**Adaptive Literature Radar**\
Weighted scientific signal for evolutionary biology, virology,
metagenomics, and cancer evolution.

LitScan is a fully automated, literature system
that:

-   Queries PubMed using structured research themes\
-   Applies recency weighting\
-   Scores papers based on domain-specific evolutionary signals\
-   Publishes a curated radar dashboard via GitHub Pages

------------------------------------------------------------------------

## Live Dashboard

https://aglucaci.github.io/litscan/

------------------------------------------------------------------------
## Conceptual Framework

LitScan is built around a core idea:

> Scientific signal decays over time --- relevance follows this.

Rather than listing papers chronologically, LitScan ranks them by:

1.  Theme priority (evolutionary selection \> general genomics)
2.  Domain signal strength (dN/dS, phylodynamics, virome, clonal
    evolution, etc.)
3.  Venue quality (MBE, Nature Microbiology, Genome Biology, etc.)
4.  Recency decay weighting

------------------------------------------------------------------------

## Research Themes

### Evolutionary Inference

-   Codon models (dN/dS, HyPhy, FEL, MEME, BUSTED)
-   Episodic / diversifying selection
-   Recombination & mosaic evolution
-   Phylodynamics & genomic epidemiology

### Virome & Urban Surveillance

-   Viral metagenomics
-   Built environment virome
-   Wastewater epidemiology
-   Pandemic preparedness & biosurveillance

### Metagenomics & Methods

-   Benchmarking
-   Pipeline reproducibility
-   Workflow engineering (Snakemake / Nextflow)
-   Large-scale atlases & reference compendia

### Cancer Evolution

-   Somatic clonal evolution
-   Tumor heterogeneity
-   Driver mutation selection
-   Mutational signatures
-   Lymphoma / leukemia evolutionary genomics

------------------------------------------------------------------------

## Architecture

    PubMed API
        ↓
    Theme-based query engine
        ↓
    Booster / negative filters
        ↓
    Scoring model
        ↓
    Recency weighting
        ↓
    Static HTML dashboard (docs/index.html)

------------------------------------------------------------------------

## Repository Structure

    .
    ├── daily_pubmed_watch_v2.py
    ├── logos/
    │   └── litscan_logo.png
    ├── docs/
    │   └── index.html
    └── .github/workflows/
        └── daily.yml

------------------------------------------------------------------------

## Automation

LitScan runs via GitHub Actions:

-   Daily signal (1-day window)
-   Weekly deep scan (7-day window)
-   Automatic commit only when output changes
-   Concurrency-safe
-   Dependency-cached for speed

Workflow:

    .github/workflows/daily.yml

------------------------------------------------------------------------

## Running Locally

``` bash
pip install requests
python daily_pubmed_watch_v2.py --days 1 --max 12
```

Output:

    docs/index.html


------------------------------------------------------------------------

## Scoring Model

Each paper receives a composite score:

    Score =
        Theme priority
      + Evolutionary signal matches
      + Venue weight
      + Recency decay factor

Key signal features include:

-   dN/dS
-   Codon models
-   HyPhy methods
-   Phylodynamics
-   Recombination
-   Virome / surveillance terms
-   Clonal evolution (cancer)
-   Mutational signatures

------------------------------------------------------------------------

## Design Philosophy

LitScan is not a feed reader.\
It is a structured scientific signal extraction system.

Designed for:

-   PI-level horizon scanning
-   Grant ideation
-   Method awareness
-   Competitive intelligence
-   Emerging pathogen surveillance
-   Cancer evolutionary genomics awareness

------------------------------------------------------------------------

## Roadmap

-   Europe PMC integration
-   Semantic Scholar signal merging
-   RSS export
-   Weekly markdown summaries
-   Email digest automation
-   Topic clustering
-   Citation velocity weighting
-   Trend detection

------------------------------------------------------------------------

## License

MIT License
