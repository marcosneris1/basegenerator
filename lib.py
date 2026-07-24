"""Base Generator — pure logic library (English).

The user's intent is captured as a `BaseConfig` dict produced by the UI checklist.
Every check that's enabled appends a specific Scala fragment to the rendered
notebook. No LLM, no description parsing — fully deterministic.

Public surface:
- `default_config(country)` — empty checklist scaffold for a country
- `TEMPLATES` — dict of pre-filled configs based on the team's example notebooks
- `validate_config(config)` — returns (errors, warnings)
- `render_scala(config)` — produces the .scala source
- `available_select_columns(config)` — columns the user can pick from in the
  Output section (varies depending on which flags/segments are enabled)
- `forbidden_tags_for(country)` — country-specific forbidden tags list
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Constants — Nubank conventions
# ---------------------------------------------------------------------------

#: BR uses *exact* tag matching via `containsAny` over an exploded
#: `customer__tags` array (from `contract-customers/customers`).
FORBIDDEN_TAGS_BR: list[str] = [
    "social_media_vip",
    "rewards/email_unsubscribers",
    "journalist",
    "user_research/...",
    "fraud_id",
    "reclame",
    "AML",
    "f&f",
    "procon",
    "suspect",
    "pep",
    "lawsuit",
    "ff_nok",
    "ff_ok",
    "gs/nubank_employee",
    "Customer_group_owner",
    "Embaixador-nu",
    "user_research/unsubscribed",
    "beta",
    "legal_fup",
    "conteudo_abusivo",
    "conteudo_ofensivo",
    "fraud_f&f",
    "cpf_irregular_suspenso",
    "cpf_irregular_cancelado",
    "obito",
    "óbito",
]

#: MX uses *substring* matching (case-insensitive) via `instr` against
#: `customer__tags` from `etl.mx__dataset.collections_daily_snapshot_sr_barriga`.
#: List is taken from the team's research/PDP notebooks (deduped).
FORBIDDEN_TAGS_MX: list[str] = [
    "social_media_vip",
    "socialmedia_vip",
    "rewards",
    "email_unsubscribers",
    "journalist",
    "user_research",
    "fraud_id",
    "fraud",
    "reclame",
    "AML",
    "aml",
    "f&f",
    "procon",
    "suspect",
    "suspicious",
    "pep",
    "lawsuit",
    "judicial-bloqueado",
    "judicialbloqueado",
    "jud ord monitor",
    "blocked",
    "bloqueado",
    "no desbloquear",
    "no_desbloquear",
    "ff_nok",
    "ff_ok",
    "gs/",
    "nubank_employee",
    "nubanker_employee",
    "Customer_group_owner",
    "Embaixador-nu",
    "unsubscribed",
    "beta",
    "cancelled",
    "cancel account request",
    "acosador_en_potencia",
]

#: spark.table() path for the MX daily snapshot — used as the source of
#: customer tags for the MX forbidden_tags filter (no `datasets()` equivalent).
MX_DAILY_SNAPSHOT_TABLE = "etl.mx__dataset.collections_daily_snapshot_sr_barriga"

#: BR enrichment datasets — used by the income-segments feature.
BR_SEGMENTS_DATASET = "dataset/br-segments-v5"

# ---------------------------------------------------------------------------
# Nubank customer tier datasets (BR-only)
# ---------------------------------------------------------------------------
# Each tier is a lookup of `customer__id`s. The "Only <tier>" filter joins the
# dataset, builds a 0/1 column, and keeps only the matched rows
# (`.where($"<col>" === 1)`) — the same rule the Roxinho filter always used.
ROXINHO_DATASET_BR = "nu-br/dataset/current-roxinho-customers"
UV_DATASET_BR = "nu-br/dataset/current-uv-customers"
NU_PLUS_DATASET_BR = "nu-br/dataset/current-nu-plus-customers"
U18_DATASET_BR = "nu-br/dataset/current-underage-customers-ids"

#: Ordered registry of the customer tiers exposed in the UI. `flag` is the
#: config key, `col` the produced 0/1 column, `var` the Scala val name.
CUSTOMER_TIERS: list[dict] = [
    {"flag": "flag_roxinho", "label": "Roxinho", "col": "roxinho",
     "var": "roxinhoCustomers", "dataset": ROXINHO_DATASET_BR},
    {"flag": "flag_uv", "label": "Ultravioleta (UV)", "col": "uv",
     "var": "uvCustomers", "dataset": UV_DATASET_BR},
    {"flag": "flag_nu_plus", "label": "Nubank+", "col": "nu_plus",
     "var": "nuPlusCustomers", "dataset": NU_PLUS_DATASET_BR},
    {"flag": "flag_u18", "label": "Under 18 (U18)", "col": "u18",
     "var": "u18Customers", "dataset": U18_DATASET_BR},
]

# ---------------------------------------------------------------------------
# BR core collections datasets — v2 migration (effective July 2026)
# ---------------------------------------------------------------------------
# The legacy combined daily snapshot (`dataset/collections-daily-snapshot`)
# carried both CC and LL and let us derive the product flags from
# `collection__origin_product`. It is being retired with NO v2 replacement.
# In v2, CC and LL live in *separate* datasets, so the source is chosen from
# the product flags: CC-only → CC v2, LL-only → LL v2, both → a union of the
# two (with per-source is_cc / is_ll tagging).

#: New official BR v2 collections datasets (Multi-Repos `incremental-table` paths).
BR_CC_DAILY_V2 = "nu-br/incremental-table/collections-cc-portfolio-daily-snapshot-v2"
BR_LL_DAILY_V2 = "nu-br/incremental-table/collections-ll-portfolio-daily-snapshot-v2"
#: Current-snapshot variants — not wired into the generator yet (daily-only for
#: now), kept here for reference / future use.
BR_CC_CURRENT_V2 = "nu-br/incremental-table/collections-cc-portfolio-current-snapshot-v2"
BR_LL_CURRENT_V2 = "nu-br/incremental-table/collections-ll-portfolio-current-snapshot-v2"

#: Legacy BR collections datasets — OBSOLETE after July 2026. Listed only in the
#: "Known datasets" reference panel so users recognise/replace them. Do NOT use
#: as a source; the renderer always emits the v2 paths above.
LEGACY_BR_COLLECTIONS: dict[str, str] = {
    "collections-daily-snapshot (CC+LL — no v2; union the v2 sets)": "dataset/collections-daily-snapshot",
    "collections-cc-portfolio-daily-snapshot → use CC v2": "nu-br/dataset/collections-cc-portfolio-daily-snapshot",
    "collections-cc-portfolio-current-snapshot → use CC current v2": "nu-br/dataset/collections-cc-portfolio-current-snapshot",
    "collections-lending-custom-snapshot → use LL v2": "nu-br/dataset/collections-lending-custom-snapshot",
    "collections-lending-current-snapshot → use LL current v2": "nu-br/dataset/collections-lending-current-snapshot",
}

# ---------------------------------------------------------------------------
# MX core collections datasets — v2 migration (mirrors the BR split model)
# ---------------------------------------------------------------------------
# Like BR, MX moves from a single combined source (the `sr-barriga/collections`
# dataset, where the product was inferred from `account__id` / `loan__id`
# presence) to *separate* CC and LL datasets. The source is chosen from the
# product flags: CC-only → CC daily, LL-only → LL daily, both/neither → a union
# of the two (with per-source is_cc / is_ll tagging). The SR Barriga daily
# snapshot is kept only as the customer-tags source for the forbidden_tags
# filter.

#: New official MX collections datasets (Multi-Repos `incremental-table` paths).
MX_CC_DAILY = "nu-mx/incremental-table/collections-cc-portfolio-daily-snapshot"
MX_LL_DAILY = "nu-mx/incremental-table/collections-ll-portfolio-daily-snapshot"

#: Legacy MX collections source — superseded by the split CC/LL datasets above.
#: The SR Barriga daily snapshot (spark.table) is still used for customer tags.
LEGACY_MX_COLLECTIONS: dict[str, str] = {
    "sr-barriga/collections (combined) → use CC/LL daily": "nu-mx/contract/sr-barriga/collections",
}

KNOWN_DATASETS: dict[str, dict[str, str]] = {
    "BR": {
        "CC daily v2 (official)": BR_CC_DAILY_V2,
        "LL daily v2 (official)": BR_LL_DAILY_V2,
        "CC current v2": BR_CC_CURRENT_V2,
        "LL current v2": BR_LL_CURRENT_V2,
        "customers": "contract-customers/customers",
        "collections-renegotiations": "dataset/collections-renegotiations",
        "personal-loans": "contract-capo/personal-loans",
        "current-roxinho-customers": ROXINHO_DATASET_BR,
        "current-uv-customers": UV_DATASET_BR,
        "current-nu-plus-customers": NU_PLUS_DATASET_BR,
        "current-underage-customers-ids": U18_DATASET_BR,
        "br-segments-v5": BR_SEGMENTS_DATASET,
    },
    "MX": {
        "CC daily (official)": MX_CC_DAILY,
        "LL daily (official)": MX_LL_DAILY,
        "sr-barriga-daily-snapshot (spark.table — tags only)": MX_DAILY_SNAPSHOT_TABLE,
        "customer-current-snapshot (spark.table)": "etl.mx__core.customer_current_snapshot",
    },
}

DEFAULT_IMPORTS_UC_PATH = "/Users/bruno.otsuka@nubank.com.br/imports_uc"

#: Default columns appended to the final select. The user can edit in the UI.
DEFAULT_SELECT_COLUMNS = ["customer__id", "prototype"]

#: Column-name conventions per country. The UI uses a single logical key
#: (e.g. "iscc") and the renderer translates to the actual column name.
#:
#: NOTE (v2 migration): this dict is the single place to update if/when the
#: official v2 schema renames a column — change the value here and the whole
#: renderer follows. v2 renamed the overdue-days column to `product__days_late`
#: (was `collection__days_late`); `collection__cured` is unchanged so far.
COLUMN_NAMES: dict[str, dict[str, str]] = {
    "BR": {
        "iscc": "is_cc",
        "isll": "is_ll",
        "days_late": "product__days_late",
        "cured": "collection__cured",
    },
    "MX": {
        "iscc": "is_cc",
        "isll": "is_ll",
        # v2: the split CC/LL datasets carry collections columns natively
        # (mirroring BR), so days_late / cured are real columns, not derived.
        "days_late": "product__days_late",
        "cured": "collection__cured",
    },
}

#: Core columns from each country's primary dataset (available pre-groupBy).
#: NOTE (BR v2): `collection__origin_product` was dropped — in v2 the product
#: is implied by which dataset (CC vs LL) a row comes from, so the column is no
#: longer used to derive the flags and may not exist in the v2 schema.
BASE_AVAILABLE_COLUMNS_BR = [
    "customer__id",
    "customer__type",
    "product__days_late",
    "collection__status",
    "collection__cured",
    "date",
]
#: NOTE (MX v2): the product is now implied by which dataset (CC vs LL) a row
#: comes from, so `account__id` / `loan__id` are no longer used to derive the
#: flags. The split datasets carry native collections columns + `prototype`.
BASE_AVAILABLE_COLUMNS_MX = [
    "customer__id",
    "customer__type",
    "product__days_late",
    "collection__cured",
    "prototype",
    "date",
]

#: Columns we know how to enrich from the BR customers dataset.
#: MX carries `prototype` natively on sr-barriga, so no enrichment is needed.
ENRICHABLE_FROM_CUSTOMERS = ["prototype"]

#: Canonical segment values used by the segment-product withColumn and by
#: multi-save split logic. Order is preserved in the rendered output.
ALL_SEGMENTS = ["cc_only", "ll_only", "multi_debt"]

#: Canonical lateness values produced by the lateness segment withColumn.
#: Order is preserved in the rendered output.
ALL_LATENESS = ["short", "long"]

#: Canonical income_segments values found in `dataset/br-segments-v5`.
#: Order is preserved in the rendered output and in UI rendering.
ALL_INCOME_SEGMENTS = ["mass_market", "super_core", "high_income"]

#: Display labels for the three income segments, shown in the UI.
INCOME_SEGMENT_LABELS = {
    "mass_market": "Mass Market",
    "super_core": "Super Core",
    "high_income": "High Income",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _col(cfg: dict, key: str) -> str:
    """Return the actual Scala column name for a logical key, country-aware."""
    return COLUMN_NAMES[cfg.get("country", "BR")][key]


def forbidden_tags_for(country: str) -> list[str]:
    """Return the canonical forbidden_tags list for a country."""
    return FORBIDDEN_TAGS_MX if country == "MX" else FORBIDDEN_TAGS_BR


def _base_available_columns(cfg: dict) -> list[str]:
    """Core columns from the primary dataset, country-aware."""
    if cfg.get("country") == "MX":
        return BASE_AVAILABLE_COLUMNS_MX
    return BASE_AVAILABLE_COLUMNS_BR


# ---------------------------------------------------------------------------
# Default config (empty checklist)
# ---------------------------------------------------------------------------

def default_config(country: str = "BR") -> dict:
    """Return an empty checklist config for a country."""
    if country == "BR":
        # BR v2: the actual source is derived from the product flags in the
        # renderer (CC v2 / LL v2 / union), so these two fields are nominal for
        # BR — kept for MX parity and shown read-only in the UI.
        primary_path = BR_CC_DAILY_V2
        primary_alias = "collectionsCc"
        days_late_high = 365
        lateness_cutoff = 65
        filter_collection_end_null = False
    else:
        # MX v2: like BR, the source is derived from the product flags in the
        # renderer (CC daily / LL daily / union), so these two fields are
        # nominal — kept for the config shape and shown as the auto source.
        primary_path = MX_CC_DAILY
        primary_alias = "collectionsCc"
        # MX team uses tighter defaults
        days_late_high = 60
        lateness_cutoff = 60
        # MX historically scoped to open collections; defaulting on
        filter_collection_end_null = True

    return {
        "country": country,
        "output_name": "my_generated_base",
        "imports_uc_path": DEFAULT_IMPORTS_UC_PATH,
        "primary_dataset_path": primary_path,
        "primary_dataset_alias": primary_alias,
        # ----- Filters -----
        "filter_snapshot_date": False,
        "snapshot_date": "2026-03-03",
        "filter_days_late_range": False,
        "days_late_low": 5,
        "days_late_high": days_late_high,
        "filter_customer_type": False,
        "customer_type": "person",
        # BR-only: read the current-snapshot datasets (latest state per
        # collection) instead of the daily snapshots.
        "use_current_snapshot": False,
        "filter_collection_end_null": filter_collection_end_null,
        # Keep only cured collections (collection__cured === 1).
        "filter_cured_only": False,
        # ----- Nubank customer tier filters (BR-only dataset join + keep
        # matched rows, same rule as Roxinho). One flag per tier. -----
        "flag_roxinho": False,
        "flag_uv": False,
        "flag_nu_plus": False,
        "flag_u18": False,
        # ----- Derived flags -----
        "flag_is_cc": False,
        "flag_is_ll": False,
        # ----- Income segments (BR-only segmentation) -----
        "segment_income": False,
        # Which income_segments values to keep — strict subset filters the
        # base; full list (all 3) just attaches the column without filtering.
        "income_segment_values": list(ALL_INCOME_SEGMENTS),
        # ----- Aggregation -----
        "groupby_customer_id": False,
        # ----- Segmentation -----
        "segment_lateness": False,
        "lateness_cutoff": lateness_cutoff,
        "segment_product": False,
        "filter_segment_only": False,
        "segment_only_value": "cc_only",  # cc_only | ll_only | multi_debt
        # ----- Multi-save -----
        # mode: "none" | "segment" | "lateness" | "segment_lateness"
        "multi_save_mode": "none",
        "multi_save_segments": list(ALL_SEGMENTS),
        "multi_save_lateness": list(ALL_LATENESS),
        # ----- Compliance -----
        "apply_forbidden_tags_filter": False,
        # ----- Output -----
        "select_columns": list(DEFAULT_SELECT_COLUMNS),
        "limit_enabled": True,
        # Default row cap, used in single-save mode and as the seed for
        # per-combo limits when multi-save is on
        "limit_value": 100000,
        # Per-combo overrides (multi-save only). Keys come from `combo_key(seg, late)`.
        # Missing keys fall back to `limit_value`.
        "limit_values": {},
    }


# ---------------------------------------------------------------------------
# Pre-built templates
# ---------------------------------------------------------------------------

def _eligibility_test_template() -> dict:
    cfg = default_config("BR")
    cfg.update({
        "output_name": "eligibility_test_base",
        "filter_snapshot_date": True,
        "snapshot_date": "2026-03-03",
        "filter_days_late_range": True,
        "days_late_low": 5,
        "days_late_high": 365,
        "filter_customer_type": True,
        "customer_type": "person",
        "flag_is_cc": True,
        "flag_is_ll": True,
        "groupby_customer_id": True,
        "segment_lateness": True,
        "lateness_cutoff": 65,
        "segment_product": True,
        "apply_forbidden_tags_filter": True,
        "select_columns": ["customer__id", "lateness", "segment", "prototype"],
        "limit_enabled": True,
        "limit_value": 100000,
    })
    return cfg


def _homepage_research_template() -> dict:
    cfg = default_config("BR")
    cfg.update({
        "output_name": "homepage_research_base",
        "filter_customer_type": True,
        "customer_type": "person",
        "apply_forbidden_tags_filter": True,
        "select_columns": ["customer__id", "prototype"],
        "limit_enabled": True,
        "limit_value": 50000,
    })
    return cfg


def _mx_collections_template() -> dict:
    """Minimal MX skeleton — open collections only, no flags or compliance."""
    cfg = default_config("MX")
    cfg.update({
        "output_name": "mx_collections_base",
        "filter_collection_end_null": True,
        "limit_enabled": True,
        "limit_value": 100000,
    })
    return cfg


def _mx_eligibility_test_template() -> dict:
    """MX equivalent of the BR eligibility test, modeled after the team's
    sr-barriga eligibility notebook. No forbidden_tags step (the team's
    eligibility notebook doesn't apply compliance)."""
    cfg = default_config("MX")
    cfg.update({
        "output_name": "eligibility_test_base_mx",
        "filter_collection_end_null": True,
        "filter_days_late_range": True,
        "days_late_low": 5,
        "days_late_high": 60,
        "flag_is_cc": True,
        "flag_is_ll": True,
        "groupby_customer_id": True,
        "segment_lateness": True,
        "lateness_cutoff": 60,
        "segment_product": True,
        "apply_forbidden_tags_filter": False,
        "select_columns": ["customer__id", "lateness", "segment", "prototype"],
        "limit_enabled": True,
        "limit_value": 100000,
    })
    return cfg


def _mx_pdp_research_template() -> dict:
    """MX research / PDP sample — adds the substring-based compliance filter
    against the SR Barriga daily snapshot, matching the team's PDP notebooks."""
    cfg = default_config("MX")
    cfg.update({
        "output_name": "mx_pdp_research_base",
        "filter_collection_end_null": True,
        "apply_forbidden_tags_filter": True,
        "select_columns": ["customer__id", "prototype"],
        "limit_enabled": True,
        "limit_value": 100000,
    })
    return cfg


TEMPLATES = {
    "Eligibility Test (BR — full)": _eligibility_test_template,
    "Homepage Research (BR — simple)": _homepage_research_template,
    "Eligibility Test (MX — full)": _mx_eligibility_test_template,
    "PDP Research (MX — with compliance)": _mx_pdp_research_template,
    "MX Collections (skeleton)": _mx_collections_template,
}


def template_names() -> list[str]:
    return list(TEMPLATES.keys())


def get_template(name: str) -> dict:
    return TEMPLATES[name]()


def summarize_config(cfg: dict) -> list[str]:
    """Plain-English bullets describing what a config (e.g. a template) ticks.

    Pure/derived from the config itself, so it stays accurate as templates
    change. Used by the UI to preview a template before applying it.
    """
    out: list[str] = [f"Country: {cfg.get('country', 'BR')}"]
    out.append(f"Source: {source_description(cfg)}")

    # ----- Filters -----
    if _use_current_snapshot(cfg):
        out.append("Current snapshot (latest state per collection)")
    if cfg.get("filter_snapshot_date"):
        out.append(f"Snapshot date = {cfg.get('snapshot_date')}")
    if cfg.get("filter_days_late_range"):
        out.append(
            f"Days late between {cfg.get('days_late_low')} and "
            f"{cfg.get('days_late_high')}"
        )
    if cfg.get("filter_customer_type"):
        out.append(f"Customer type = {cfg.get('customer_type')}")
    if cfg.get("filter_collection_end_null"):
        out.append("Only open collections (collection__end is null)")
    if cfg.get("filter_cured_only"):
        out.append("Cured collections only (collection__cured = 1)")
    for tier in _selected_tiers(cfg):
        out.append(f"Customer tier: {tier['label']} only")

    # ----- Derived flags -----
    cc, ll = cfg.get("flag_is_cc"), cfg.get("flag_is_ll")
    if cc and ll:
        out.append("Flags: is_cc + is_ll (both products)")
    elif cc:
        out.append("Flag: is_cc only → base filtered to credit-card")
    elif ll:
        out.append("Flag: is_ll only → base filtered to lending")

    # ----- Aggregation -----
    if cfg.get("groupby_customer_id"):
        out.append("Group by customer__id (one row per customer)")

    # ----- Segmentation -----
    if cfg.get("segment_lateness"):
        out.append(
            f"Lateness split (cutoff {cfg.get('lateness_cutoff')}d → short / long)"
        )
    if cfg.get("segment_product"):
        out.append("Segment: cc_only / ll_only / multi_debt")
    if cfg.get("segment_income"):
        picked = _income_segments_picked(cfg)
        if not picked or len(picked) == len(ALL_INCOME_SEGMENTS):
            out.append("Income segments column (all values, no filter)")
        else:
            labels = ", ".join(INCOME_SEGMENT_LABELS[v] for v in picked)
            out.append(f"Income segments: {labels} only")

    # ----- Split mode / multi-save -----
    if cfg.get("multi_save_mode", "none") != "none":
        names = multi_save_names(cfg)
        out.append(f"Multi-save → {len(names)} bases ({cfg['multi_save_mode']})")
    elif cfg.get("filter_segment_only"):
        out.append(f"Keep only segment = {cfg.get('segment_only_value')}")

    # ----- Compliance -----
    if cfg.get("apply_forbidden_tags_filter"):
        n = len(forbidden_tags_for(cfg.get("country", "BR")))
        out.append(f"forbidden_tags compliance filter ({n} tags)")

    # ----- Output -----
    cols = cfg.get("select_columns") or []
    if cols:
        out.append(f"Columns: {', '.join(cols)}")
    if cfg.get("limit_enabled"):
        out.append(f"Sample size: {cfg.get('limit_value', 0):,} rows")

    return out


# ---------------------------------------------------------------------------
# Available columns — for the "select columns" multiselect in the UI
# ---------------------------------------------------------------------------

def available_select_columns(cfg: dict) -> list[str]:
    """Columns the user is allowed to pick in the Output section.

    Includes:
    - customer__id (always)
    - core columns from the primary dataset (only if no groupBy is applied —
      groupBy drops them)
    - flag columns (is_cc, is_ll) — if those flags are enabled
    - days_late / product__days_late — native pre-groupBy from source;
      post-groupBy only if aggregated
    - segmentation columns (lateness, segment) — if those segments are enabled
    - prototype — always (enriched from customers in BR, native in MX)

    The list is deduplicated and ordered for predictable UI rendering.
    """
    cols: list[str] = ["customer__id"]
    grouped = cfg.get("groupby_customer_id", False)
    is_mx = cfg.get("country") == "MX"
    iscc = _col(cfg, "iscc")
    isll = _col(cfg, "isll")
    days_late_col = _col(cfg, "days_late")

    # Core source columns survive only if not aggregated away
    if not grouped:
        for c in _base_available_columns(cfg):
            if c not in cols:
                cols.append(c)

    # Derived flags (after groupBy these survive as max())
    if cfg.get("flag_is_cc") and iscc not in cols:
        cols.append(iscc)
    if cfg.get("flag_is_ll") and isll not in cols:
        cols.append(isll)

    # days_late column availability
    needs_days_late = (
        cfg.get("filter_days_late_range") or cfg.get("segment_lateness")
    )
    # product__days_late exists natively pre-groupBy (BR + MX); it's in the
    # core columns above when not grouped, and survives a groupBy only if
    # aggregated (the days_late filter or the lateness segment is on).
    if grouped and needs_days_late and days_late_col not in cols:
        cols.append(days_late_col)

    # Segments
    if cfg.get("segment_lateness") and "lateness" not in cols:
        cols.append("lateness")
    if cfg.get("segment_product") and "segment" not in cols:
        cols.append("segment")

    # Prototype availability
    if is_mx:
        # MX carries prototype natively on sr-barriga, and the MX groupBy
        # includes prototype as a key, so it survives in both paths
        if "prototype" not in cols:
            cols.append("prototype")
    else:
        # BR: enriched from customers via inner join at save time
        for c in ENRICHABLE_FROM_CUSTOMERS:
            if c not in cols:
                cols.append(c)

    # Customer tier flags — each produces a 0/1 column at save time from a
    # left join + coalesce (then filtered to 1).
    for tier in _enabled_tiers(cfg):
        if tier["col"] not in cols:
            cols.append(tier["col"])

    # Income segments — categorical column attached from br-segments-v5
    # when the Income segments segmentation is on. Filtering by subset
    # is handled in `_render_one_save`; the column is exposed either way.
    if cfg.get("segment_income") and "income_segments" not in cols:
        cols.append("income_segments")

    return cols


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(cfg: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    name = cfg.get("output_name", "")
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        warnings.append(
            f"Output name '{name}' should be snake_case "
            "(lowercase letters, numbers, underscores; starts with a letter)."
        )

    country = cfg.get("country")
    # Neither BR nor MX uses a manually-entered primary path anymore — the
    # source is derived from the product flags (CC / LL / union), so there's
    # no path to validate.

    # Compliance heads-up: PF bases usually need forbidden_tags
    if (
        cfg.get("filter_customer_type")
        and cfg.get("customer_type") == "person"
        and not cfg.get("apply_forbidden_tags_filter")
    ):
        label = "MX" if country == "MX" else "BR"
        warnings.append(
            f"{label} base for individuals (person) without the forbidden_tags filter. "
            "This is usually required for outbound/research bases — confirm if the "
            "exception is intentional (e.g. eligibility tests typically skip it)."
        )

    if cfg.get("filter_days_late_range"):
        low, high = cfg.get("days_late_low"), cfg.get("days_late_high")
        if low is None or high is None:
            errors.append("Days_late range filter is on but values are missing.")
        elif low > high:
            errors.append(f"Min days_late ({low}) is greater than max ({high}).")

    if cfg.get("segment_product"):
        if not (cfg.get("flag_is_cc") and cfg.get("flag_is_ll")):
            errors.append(
                "Segmentation by product (cc_only/ll_only/multi_debt) needs "
                "BOTH the is_cc AND is_ll flags enabled."
            )

    if cfg.get("filter_segment_only"):
        if not cfg.get("segment_product"):
            errors.append(
                "Filter by segment is on but segmentation by product isn't. "
                "Enable 'segment — cc_only / ll_only / multi_debt' first."
            )
        valid_values = {"cc_only", "ll_only", "multi_debt"}
        if cfg.get("segment_only_value") not in valid_values:
            errors.append(
                f"Invalid segment value '{cfg.get('segment_only_value')}'. "
                f"Must be one of: {', '.join(sorted(valid_values))}."
            )

    mode = cfg.get("multi_save_mode", "none")
    if mode != "none":
        if cfg.get("filter_segment_only"):
            errors.append(
                "Both 'filter to one segment' and 'multi-save' are on. Pick one — "
                "they're mutually exclusive (the UI radio normally enforces this)."
            )
        needs_segment = mode in ("segment", "segment_lateness")
        needs_lateness = mode in ("lateness", "segment_lateness")
        if needs_segment and not cfg.get("segment_product"):
            errors.append(
                f"Multi-save mode '{mode}' needs the segment classification on. "
                "Enable 'segment — cc_only / ll_only / multi_debt' first."
            )
        if needs_lateness and not cfg.get("segment_lateness"):
            errors.append(
                f"Multi-save mode '{mode}' needs the lateness segment on. "
                "Enable 'lateness — short / long' first."
            )
        if needs_segment:
            segs = [s for s in (cfg.get("multi_save_segments") or []) if s in ALL_SEGMENTS]
            if not segs:
                errors.append(
                    "Multi-save: pick at least one segment from cc_only / ll_only / multi_debt."
                )
        if needs_lateness:
            lates = [l for l in (cfg.get("multi_save_lateness") or []) if l in ALL_LATENESS]
            if not lates:
                errors.append(
                    "Multi-save: pick at least one lateness value (short and/or long)."
                )
        # Sanity hint: 1 combo total = same as a single filter, not really multi
        if len(_multi_save_combos(cfg)) == 1:
            warnings.append(
                "Multi-save will produce only one table. This is the same as a "
                "single filtered save — consider switching to 'Filter to one segment' "
                "or selecting more values."
            )

    # GroupBy with nothing to aggregate — only meaningful for BR; for MX the
    # groupBy keys include prototype, which is itself useful (dedupe by pair).
    if (
        country == "BR"
        and cfg.get("groupby_customer_id")
        and not (
            cfg.get("flag_is_cc")
            or cfg.get("flag_is_ll")
            or cfg.get("filter_days_late_range")
            or cfg.get("segment_lateness")
        )
    ):
        warnings.append(
            "GroupBy is on but there's nothing to aggregate. The Scala will skip the .agg() call."
        )

    if cfg.get("filter_snapshot_date"):
        d = cfg.get("snapshot_date", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            errors.append(f"Snapshot date '{d}' must be in ISO format (YYYY-MM-DD).")

    if cfg.get("limit_enabled"):
        lv = cfg.get("limit_value")
        if lv is None or lv <= 0:
            errors.append("Sample size is on but the value is invalid.")
        elif lv > 1_000_000:
            warnings.append(
                f"Sample size is very large ({lv:,}). Test bases usually stay under ~150,000."
            )

        # Per-combo overrides (multi-save only). The renderer falls back to
        # `limit_value` for any missing/invalid key, so we only flag combos
        # that are actually going to be produced.
        if cfg.get("multi_save_mode", "none") != "none":
            overrides = cfg.get("limit_values") or {}
            base_name = cfg.get("output_name", "")
            for seg, late in _multi_save_combos(cfg):
                key = combo_key(seg, late)
                if key not in overrides:
                    continue
                v = overrides[key]
                table = base_name + _combo_suffix(seg, late)
                if not isinstance(v, int) or v <= 0:
                    errors.append(
                        f"Sample size for `{table}` is invalid."
                    )
                elif v > 1_000_000:
                    warnings.append(
                        f"Sample size for `{table}` is very large ({v:,}). "
                        "Test bases usually stay under ~150,000."
                    )

    # The cured-only filter interacts with the open-collection filter: keeping
    # only cured collections AND only open ones (collection__end is null) will
    # almost certainly yield an empty base.
    if cfg.get("filter_cured_only") and cfg.get("filter_collection_end_null"):
        warnings.append(
            "'Cured collections only' and 'only open collections' are both on. "
            "Cured collections have usually ended (collection__end is set), so "
            "combining these filters will likely produce an empty base — disable one."
        )

    # BR-only enrichments — warn if turned on for MX
    if country == "MX":
        for tier in _selected_tiers(cfg):
            warnings.append(
                f"'{tier['label']} only' filter is on but the country is MX. "
                f"The dataset (`{tier['dataset']}`) is BR-only — disable this "
                "or switch to BR."
            )
    if country == "MX" and cfg.get("segment_income"):
        warnings.append(
            "Income segments segmentation is on but the country is MX. The dataset "
            "`dataset/br-segments-v5` is BR-only — disable this or switch to BR."
        )

    # Income segments: block on empty selection (would filter out everything)
    if cfg.get("segment_income"):
        picked = _income_segments_picked(cfg)
        if not picked:
            errors.append(
                "Income segments is on but no values are selected. Pick at least one "
                "of Mass Market / Super Core / High Income (or untick the feature)."
            )

    if not cfg.get("select_columns"):
        errors.append("The list of columns to keep in the output is empty.")
    else:
        # Warn about typos: any selected column that isn't available
        avail = set(available_select_columns(cfg))
        unknown = [c for c in cfg["select_columns"] if c not in avail]
        if unknown:
            warnings.append(
                f"Some selected columns aren't recognized: {', '.join(unknown)}. "
                "If you typed a column name manually, double-check the spelling."
            )

    return errors, warnings


# ---------------------------------------------------------------------------
# Scala renderer — assembles fragments based on enabled checks
# ---------------------------------------------------------------------------

def _columns_produced_by_base(cfg: dict) -> set[str]:
    """Set of columns that exist on `val base` after all transformations.

    Used to detect which select_columns need re-attachment from customers
    (e.g. when groupBy drops `prototype` and the user selected it — BR only).
    """
    cols: set[str] = {"customer__id"}
    grouped = cfg.get("groupby_customer_id", False)
    is_mx = cfg.get("country") == "MX"
    iscc = _col(cfg, "iscc")
    isll = _col(cfg, "isll")
    days_late_col = _col(cfg, "days_late")

    if grouped:
        if cfg.get("flag_is_cc"):
            cols.add(iscc)
        if cfg.get("flag_is_ll"):
            cols.add(isll)
        if cfg.get("filter_days_late_range") or cfg.get("segment_lateness"):
            cols.add(days_late_col)
        if is_mx:
            # MX groupBy keys include prototype
            cols.add("prototype")
    else:
        for c in _base_available_columns(cfg):
            cols.add(c)
        if cfg.get("flag_is_cc"):
            cols.add(iscc)
        if cfg.get("flag_is_ll"):
            cols.add(isll)

    # Segments are added AFTER groupBy
    if cfg.get("segment_lateness"):
        cols.add("lateness")
    if cfg.get("segment_product"):
        cols.add("segment")

    # Roxinho and income_segments are joined at save time, not at base level,
    # so we don't add them here (they aren't on `val base`).

    return cols


def _columns_to_enrich(cfg: dict) -> list[str]:
    """Columns to inner-join from customers, in the order they appear in
    select_columns. BR only — MX carries prototype natively on sr-barriga.
    """
    if cfg.get("country") != "BR":
        return []
    select_cols = cfg.get("select_columns") or []
    produced = _columns_produced_by_base(cfg)
    return [
        c for c in select_cols
        if c in ENRICHABLE_FROM_CUSTOMERS and c not in produced
    ]


def _wants_cc_ll(cfg: dict) -> tuple[bool, bool]:
    """v2 split model (BR + MX): which product datasets the base needs, as
    (want_cc, want_ll), derived from the product flags.

    - is_cc only            → (True, False)  → CC daily only
    - is_ll only            → (False, True)  → LL daily only
    - both flags            → (True, True)   → union(CC, LL)
    - neither flag          → (True, True)   → union = all collections
      (the replacement for the legacy combined snapshot when product isn't split)
    """
    cc = bool(cfg.get("flag_is_cc"))
    ll = bool(cfg.get("flag_is_ll"))
    if not cc and not ll:
        return True, True
    return cc, ll


def _use_current_snapshot(cfg: dict) -> bool:
    """BR-only: whether to read the current-snapshot datasets (latest state
    per collection) instead of the daily snapshots."""
    return cfg.get("country") == "BR" and bool(cfg.get("use_current_snapshot"))


def _collections_v2_paths(cfg: dict) -> tuple[str, str]:
    """Return the (cc_path, ll_path) collections datasets for the country."""
    if cfg.get("country") == "MX":
        return MX_CC_DAILY, MX_LL_DAILY
    if _use_current_snapshot(cfg):
        return BR_CC_CURRENT_V2, BR_LL_CURRENT_V2
    return BR_CC_DAILY_V2, BR_LL_DAILY_V2


def _is_collections_union(cfg: dict) -> bool:
    """Whether the base is a union of the CC and LL datasets (BR or MX)."""
    if cfg.get("country") not in ("BR", "MX"):
        return False
    cc, ll = _wants_cc_ll(cfg)
    return cc and ll


def _br_latest_date_ref(cfg: dict) -> str:
    """The val name to read `max(date)` from for the income-segments lookup.
    Prefers the CC v2 dataset, falls back to LL v2. (BR-only feature.)"""
    cc, _ = _wants_cc_ll(cfg)
    return "collectionsCc" if cc else "collectionsLl"


def source_description(cfg: dict) -> str:
    """Human-readable description of the v2 source the renderer will use,
    based on the product flags (country-aware). For the UI."""
    cc, ll = _wants_cc_ll(cfg)
    neither = not cfg.get("flag_is_cc") and not cfg.get("flag_is_ll")
    if cfg.get("country") == "MX":
        label = "CC daily / LL daily"
    elif _use_current_snapshot(cfg):
        label = "CC current v2 / LL current v2"
    else:
        label = "CC v2 / LL v2"
    cc_name, ll_name = (lbl.strip() for lbl in label.split("/"))
    if cc and ll:
        suffix = " (no product flag set → all collections)" if neither else ""
        return f"Union of {cc_name} + {ll_name}{suffix}"
    if cc:
        return f"{cc_name} (credit-card)"
    return f"{ll_name} (lending)"


def _needs_customers_dataset(cfg: dict) -> bool:
    """BR-only: whether the rendered Scala needs `val customers = ...`."""
    if cfg.get("country") != "BR":
        return False
    return bool(cfg.get("apply_forbidden_tags_filter")) or bool(_columns_to_enrich(cfg))


def _needs_mx_daily_snapshot(cfg: dict) -> bool:
    """MX-only: whether we need to declare the SR Barriga daily snapshot
    (used as the source for the forbidden_tags filter)."""
    return cfg.get("country") == "MX" and bool(cfg.get("apply_forbidden_tags_filter"))


def _selected_tiers(cfg: dict) -> list[dict]:
    """Customer tiers whose filter flag is on (regardless of country).

    Used for the summary and the MX warning. Rendering uses `_enabled_tiers`,
    which additionally requires BR (the tier datasets are BR-only).
    """
    return [t for t in CUSTOMER_TIERS if cfg.get(t["flag"])]


def _enabled_tiers(cfg: dict) -> list[dict]:
    """BR-only: customer tiers we should actually join/filter and declare a
    `val <tier>Customers = ...` lookup for."""
    if cfg.get("country") != "BR":
        return []
    return _selected_tiers(cfg)




def _needs_br_segments_dataset(cfg: dict) -> bool:
    """BR-only: whether we need to declare `val brSegments = ...`.

    Driven by the Income segments segmentation feature — joining the segments
    dataset attaches the `income_segments` column and (optionally) filters by
    a subset of values.
    """
    return cfg.get("country") == "BR" and bool(cfg.get("segment_income"))


def _income_segments_picked(cfg: dict) -> list[str]:
    """Validated, order-preserving list of picked income_segment values."""
    raw = cfg.get("income_segment_values") or []
    return [v for v in ALL_INCOME_SEGMENTS if v in raw]


def _income_segments_filter_clause(cfg: dict) -> str | None:
    """Return the Scala `.where(...)` line for the income_segments filter,
    or `None` if no filter should be applied.

    - Empty selection → caller should already be blocking via validation
    - All 3 picked → no filter (just attach the column)
    - 1 picked → use `=== "value"` (more readable than 1-element isin)
    - 2 picked → use `.isin("a", "b")`
    """
    picked = _income_segments_picked(cfg)
    if not picked or len(picked) == len(ALL_INCOME_SEGMENTS):
        return None
    if len(picked) == 1:
        return f'  .where($"income_segments" === "{picked[0]}")'
    vals = ", ".join(f'"{v}"' for v in picked)
    return f'  .where($"income_segments".isin({vals}))'


def _render_forbidden_tags_block(cfg: dict) -> list[str]:
    """Dispatch to the BR or MX flavor of the compliance filter."""
    if cfg.get("country") == "MX":
        return _render_forbidden_tags_block_mx()
    return _render_forbidden_tags_block_br()


def _render_forbidden_tags_block_br() -> list[str]:
    L = []
    L.append("// COMMAND ----------")
    L.append("")
    L.append("// MAGIC %md ## Forbidden tags filter (BR compliance)")
    L.append("")
    L.append("// COMMAND ----------")
    L.append("")
    L.append("val forbiddenTags = Seq(")
    for i, tag in enumerate(FORBIDDEN_TAGS_BR):
        comma = "," if i < len(FORBIDDEN_TAGS_BR) - 1 else ""
        L.append(f'  "{tag}"{comma}')
    L.append(")")
    L.append("")
    L.append("val forbiddenTagsCustomers = customers")
    L.append('  .withColumn("tag", explode($"customer__tags"))')
    L.append('  .withColumn("has_forbidden_tag", containsAny($"tag", forbiddenTags).cast("Int"))')
    L.append('  .groupBy("customer__id")')
    L.append('  .agg(maximo("has_forbidden_tag"))')
    L.append('  .where($"has_forbidden_tag" === 1)')
    L.append("")
    return L


def _render_forbidden_tags_block_mx() -> list[str]:
    """MX uses substring matching (case-insensitive) over customer__tags from
    the SR Barriga daily snapshot. Mirrors the pattern used in the team's
    PDP research notebooks."""
    L = []
    L.append("// COMMAND ----------")
    L.append("")
    L.append("// MAGIC %md ## Forbidden tags filter (MX compliance)")
    L.append("// MAGIC ")
    L.append("// MAGIC Substring-matches `customer__tags` from the SR Barriga daily snapshot.")
    L.append("// MAGIC Lowercases tags first, then checks each one against the substring list.")
    L.append("")
    L.append("// COMMAND ----------")
    L.append("")
    L.append("val substringsToRemove = Seq(")
    for i, tag in enumerate(FORBIDDEN_TAGS_MX):
        comma = "," if i < len(FORBIDDEN_TAGS_MX) - 1 else ""
        L.append(f'  "{tag}"{comma}')
    L.append(")")
    L.append("")
    L.append("val forbiddenTagsCustomers = srBarrigaDailySnapshot")
    L.append('  .withColumn("lowered_tags", expr("transform(customer__tags, x -> lower(x))"))')
    L.append('  .withColumn(')
    L.append('    "has_forbidden_tag",')
    L.append('    expr(')
    L.append('      s"""')
    L.append('      EXISTS(lowered_tags, x -> ${substringsToRemove.map(s => s"instr(x, \'$s\') > 0").mkString(" OR ")})')
    L.append('      """')
    L.append('    )')
    L.append('  )')
    L.append('  .where($"has_forbidden_tag" === true)')
    L.append('  .select($"customer__id").distinct')
    L.append("")
    return L


def _render_filters(cfg: dict) -> list[str]:
    """Render the where-clause chain that builds `val base`.

    In the v2 split model `days_late` is a native column for both countries
    (`product__days_late`), so there's no derivation step anymore.
    """
    L = []
    days_late_col = _col(cfg, "days_late")

    if cfg.get("filter_snapshot_date"):
        L.append(f'  .where($"date" === "{cfg["snapshot_date"]}")')
    if cfg.get("filter_customer_type"):
        L.append(f'  .where($"customer__type" === "{cfg["customer_type"]}")')
    if cfg.get("filter_collection_end_null"):
        L.append('  .where($"collection__end".isNull)')
    if cfg.get("filter_cured_only"):
        L.append(f'  .where($"{_col(cfg, "cured")}" === 1)')

    if cfg.get("filter_days_late_range"):
        L.append(f'  .where($"{days_late_col}" >= {cfg["days_late_low"]})')
        L.append(f'  .where($"{days_late_col}" <= {cfg["days_late_high"]})')

    return L


def _render_groupby(cfg: dict) -> list[str]:
    """Render the groupBy + agg block.

    BR: groupBy("customer__id"), agg uses max(...).as(...).
    MX: groupBy("customer__id", "prototype") to carry prototype through
    (sr-barriga has it natively); agg uses Nubank's `maximo()` helper without
    explicit alias (column name preserved).
    """
    if not cfg.get("groupby_customer_id"):
        return []

    is_mx = cfg.get("country") == "MX"
    iscc = _col(cfg, "iscc")
    isll = _col(cfg, "isll")
    days_late_col = _col(cfg, "days_late")

    aggs = []
    if cfg.get("flag_is_cc"):
        aggs.append(iscc)
    if cfg.get("flag_is_ll"):
        aggs.append(isll)
    if cfg.get("filter_days_late_range") or cfg.get("segment_lateness"):
        aggs.append(days_late_col)

    L = []
    if is_mx:
        L.append('  .groupBy("customer__id", "prototype")')
    else:
        L.append('  .groupBy("customer__id")')

    if aggs:
        L.append("  .agg(")
        for i, a in enumerate(aggs):
            comma = "," if i < len(aggs) - 1 else ""
            if is_mx:
                L.append(f'    maximo("{a}"){comma}')
            else:
                L.append(f'    max("{a}").as("{a}"){comma}')
        L.append("  )")
    return L


def _render_segments(cfg: dict) -> list[str]:
    L = []
    iscc = _col(cfg, "iscc")
    isll = _col(cfg, "isll")
    days_late_col = _col(cfg, "days_late")

    if cfg.get("segment_lateness"):
        cutoff = cfg.get("lateness_cutoff", 65)
        L.append(
            f'  .withColumn("lateness", '
            f'when($"{days_late_col}" >= {cutoff}, lit("long")).otherwise(lit("short")))'
        )
    if cfg.get("segment_product"):
        L.append('  .withColumn("segment",')
        L.append(f'    when($"{iscc}" >= 1 && $"{isll}" >= 1, "multi_debt")')
        L.append(f'    .when($"{iscc}" === 0 && $"{isll}" >= 1, "ll_only")')
        L.append(f'    .when($"{iscc}" >= 1 && $"{isll}" === 0, "cc_only").otherwise("error")')
        L.append("  )")
    # NOTE: segment filtering (single-segment or multi-save split) is applied
    # at save time in `_render_one_save`, not here. This keeps the `val base`
    # chain reusable across all save variants.
    return L


def _multi_save_combos(cfg: dict) -> list[tuple[str | None, str | None]]:
    """Return the list of (segment, lateness) combos that the multi-save
    feature will produce. `None` means "don't filter on this dimension".

    Mode determines which dimensions are split:
    - "none"             → []                       (single-save path)
    - "segment"          → [(s, None) for s in S]
    - "lateness"         → [(None, l) for l in L]
    - "segment_lateness" → [(s, l) for s in S for l in L]   (cross product)
    """
    mode = cfg.get("multi_save_mode", "none")
    if mode == "none":
        return []

    segs = [s for s in (cfg.get("multi_save_segments") or []) if s in ALL_SEGMENTS]
    lates = [l for l in (cfg.get("multi_save_lateness") or []) if l in ALL_LATENESS]

    if mode == "segment":
        return [(s, None) for s in segs]
    if mode == "lateness":
        return [(None, l) for l in lates]
    if mode == "segment_lateness":
        return [(s, l) for s in segs for l in lates]
    return []


def _combo_suffix(segment: str | None, lateness: str | None) -> str:
    """Build the table-name suffix for a (segment, lateness) combo.

    Order matches the team's notebook: `_<segment>_<lateness>`.
    """
    parts: list[str] = []
    if segment is not None:
        parts.append(segment)
    if lateness is not None:
        parts.append(lateness)
    return "_" + "_".join(parts) if parts else ""


def combo_key(segment: str | None, lateness: str | None) -> str:
    """Stable string key for a (segment, lateness) combo. Used as a dict
    key in `limit_values` for per-save row limits. Same shape as the
    table-name suffix but *without* the leading underscore (so it works
    cleanly as a dict key and a Streamlit widget key)."""
    parts: list[str] = []
    if segment is not None:
        parts.append(segment)
    if lateness is not None:
        parts.append(lateness)
    return "_".join(parts)


def multi_save_combos(cfg: dict) -> list[tuple[str | None, str | None]]:
    """Public accessor for the (segment, lateness) tuples that the multi-save
    feature will produce. Exposed for the UI so it can render per-save
    controls (e.g. per-combo row limits)."""
    return _multi_save_combos(cfg)


def _limit_for_combo(
    cfg: dict, segment: str | None, lateness: str | None
) -> int | None:
    """Return the row limit for a specific combo, or `None` when no limit
    should be applied.

    Lookup order:
    1. If `limit_enabled` is off → `None` (no `.limit(...)` clause)
    2. Per-combo override in `cfg["limit_values"]` keyed by `combo_key(...)`
    3. Fall back to the global `cfg["limit_value"]`
    """
    if not cfg.get("limit_enabled"):
        return None
    overrides = cfg.get("limit_values") or {}
    key = combo_key(segment, lateness)
    if key and key in overrides:
        return overrides[key]
    return cfg.get("limit_value")


def multi_save_names(cfg: dict) -> list[str]:
    """Return the list of table names that will be produced for the current
    config. One entry per save (1 for single-save mode, N for multi-save).
    Useful for previewing in the UI."""
    base = cfg.get("output_name", "")
    combos = _multi_save_combos(cfg)
    if not combos:
        return [base]
    return [base + _combo_suffix(seg, late) for seg, late in combos]


def _render_one_save(
    cfg: dict,
    segment_override: str | None = None,
    lateness_override: str | None = None,
    name_suffix: str = "",
) -> list[str]:
    """Render one save chain: `base → joins → filters → select → limit → save → display`.

    Used both for single-save (called once) and multi-save (called once per
    combo with `segment_override`/`lateness_override` and `name_suffix`).
    The order of where-clauses (lateness before segment) matches the team's
    notebook for stylistic consistency — Spark optimizes either order the same.
    """
    output_name = cfg["output_name"] + name_suffix

    L: list[str] = []
    L.append("// COMMAND ----------")
    L.append("")
    L.append("base")

    if cfg.get("apply_forbidden_tags_filter"):
        L.append('  .join(forbiddenTagsCustomers, Seq("customer__id"), "leftanti")')

    enrich_cols = _columns_to_enrich(cfg)
    if enrich_cols:
        # `customers` (contract-customers/customers) is contract-level with
        # no date/recency column, so multiple rows per customer are possible.
        # The enriched columns (e.g. `prototype`) are stable customer
        # attributes — identical across a customer's rows — so deduping on
        # `customer__id` is safe and keeps the base at one row per customer.
        L.append("  .join(customers.select(")
        L.append('    $"customer__id",')
        for i, c in enumerate(enrich_cols):
            comma = "," if i < len(enrich_cols) - 1 else ""
            L.append(f'    $"{c}"{comma}')
        L.append('  ).dropDuplicates(Seq("customer__id")), Seq("customer__id"))')

    # Nubank customer tier filters: for each enabled tier, left join + coalesce
    # builds the 0/1 column, then `.where($"<col>" === 1)` drops everyone who
    # isn't in that tier's list. Multiple tiers combine as AND.
    for tier in _enabled_tiers(cfg):
        col = tier["col"]
        L.append(f'  .join({tier["var"]}, Seq("customer__id"), "left")')
        L.append(f'  .withColumn("{col}", coalesce($"{col}", lit(0)))')
        L.append(f'  .where($"{col}" === 1)')

    # Income segments: left join brings the `income_segments` column.
    # When the user selects a strict subset of the 3 values, also filter
    # to keep only those rows.
    if _needs_br_segments_dataset(cfg):
        L.append('  .join(brSegments, Seq("customer__id"), "left")')
        clause = _income_segments_filter_clause(cfg)
        if clause is not None:
            L.append(clause)

    # Lateness filter (multi-save override only; lateness has no single-filter
    # equivalent in the current UI)
    if lateness_override is not None:
        L.append(f'  .where($"lateness" === "{lateness_override}")')

    # Segment filter: explicit override wins (multi-save); otherwise fall
    # back to the single filter_segment_only setting.
    if segment_override is not None:
        L.append(f'  .where($"segment" === "{segment_override}")')
    elif cfg.get("filter_segment_only"):
        value = cfg.get("segment_only_value", "cc_only")
        L.append(f'  .where($"segment" === "{value}")')

    cols = cfg.get("select_columns") or []
    if cols:
        L.append("  .select(")
        for i, c in enumerate(cols):
            comma = "," if i < len(cols) - 1 else ""
            L.append(f'    $"{c}"{comma}')
        L.append("  )")
    limit = _limit_for_combo(cfg, segment_override, lateness_override)
    if limit is not None:
        L.append(f'  .limit({limit})')
    L.append(f'  .save("{output_name}")')
    L.append("")
    L.append("// COMMAND ----------")
    L.append("")
    L.append(f'table("{output_name}")')
    L.append("  .d")
    L.append("")
    return L


def _render_save_block(cfg: dict) -> list[str]:
    """Render the Save section — single save or N saves split by combo.

    Multi-save mode emits one `base → … → save("name_<combo>")` chain per
    combo from `_multi_save_combos(cfg)`. The upstream `val base` is shared
    across all saves, so filters/flags/groupBy/segments are declared once.
    """
    mode = cfg.get("multi_save_mode", "none")
    multi = mode != "none"

    L: list[str] = []
    L.append("// COMMAND ----------")
    L.append("")
    if multi:
        label = {
            "segment": "Save (multi: one base per segment)",
            "lateness": "Save (multi: one base per lateness value)",
            "segment_lateness": "Save (multi: one base per segment × lateness)",
        }.get(mode, "Save (multi)")
        L.append(f"// MAGIC %md ## {label}")
    else:
        L.append("// MAGIC %md ## Save")
    L.append("")

    if not multi:
        L.extend(_render_one_save(cfg))
        return L

    for seg, late in _multi_save_combos(cfg):
        L.extend(_render_one_save(
            cfg,
            segment_override=seg,
            lateness_override=late,
            name_suffix=_combo_suffix(seg, late),
        ))

    return L


def _render_header(cfg: dict) -> list[str]:
    L = []
    L.append("// Databricks notebook source")
    L.append(f'// MAGIC %run {cfg["imports_uc_path"]}')
    L.append("")
    L.append("// COMMAND ----------")
    L.append("")

    if cfg["country"] == "MX":
        L.append("// MAGIC %md # MX — auto-generated")
        L.append("// MAGIC ")
        if cfg.get("apply_forbidden_tags_filter"):
            L.append(
                "// MAGIC Compliance: substring-match over `customer__tags` from the "
                "SR Barriga daily snapshot (case-insensitive)."
            )
        else:
            L.append(
                "// MAGIC ⚠️ No compliance filter applied. This is normal for eligibility "
                "tests; outbound/research bases usually need the MX forbidden_tags step."
            )
        L.append("")
        L.append("// COMMAND ----------")
        L.append("")
    else:
        L.append("// MAGIC %md # BR — auto-generated")
        L.append("")
        L.append("// COMMAND ----------")
        L.append("")

    L.append('spark.conf.set("spark.databricks.remoteFiltering.blockSelfJoins", "false")')
    L.append("")
    return L


def _render_datasets_block(cfg: dict) -> list[str]:
    L = []
    L.append("// COMMAND ----------")
    L.append("")
    L.append("// MAGIC %md ## Datasets")
    L.append("")
    L.append("// COMMAND ----------")
    L.append("")
    # v2 split model (BR + MX): declare the product dataset(s) the base needs.
    # The flag tagging and (when both) the union happen in the base block.
    cc, ll = _wants_cc_ll(cfg)
    cc_path, ll_path = _collections_v2_paths(cfg)
    if cc:
        L.append(f'val collectionsCc = datasets("{cc_path}")')
    if ll:
        L.append(f'val collectionsLl = datasets("{ll_path}")')
    if _needs_customers_dataset(cfg):
        L.append('val customers = datasets("contract-customers/customers")')
    if _needs_mx_daily_snapshot(cfg):
        # The MX daily snapshot lives outside the `datasets()` helper, accessed
        # via spark.table directly (matches what the team's notebooks do).
        L.append(f'val srBarrigaDailySnapshot = spark.table("{MX_DAILY_SNAPSHOT_TABLE}")')
    for tier in _enabled_tiers(cfg):
        # Tier lookup: deduped customer__id list with a constant 1, so a left
        # join + coalesce gives <col> = 1 (matched) or 0 (unmatched).
        L.append(f'val {tier["var"]} = datasets("{tier["dataset"]}")')
        L.append('  .select($"customer__id").distinct')
        L.append(f'  .withColumn("{tier["col"]}", lit(1))')
    if _needs_br_segments_dataset(cfg):
        # MM income segments — recency is driven by `income_month` (the
        # reference month of the estimated income): grab the latest snapshot
        # date from the primary dataset and keep only income rows at or after
        # it. `dropDuplicates` is the one-row-per-customer safety net so the
        # left join can't re-inflate a deduped base.
        ref = _br_latest_date_ref(cfg)
        L.append(
            f'val latestDate = {ref}.agg(max("date")).collect()(0).getDate(0)'
        )
        L.append(
            f'val brSegments = datasets("{BR_SEGMENTS_DATASET}")'
        )
        L.append('  .where($"income_month" >= latestDate)')
        L.append('  .select($"customer__id", $"income_segments")')
        L.append('  .dropDuplicates(Seq("customer__id"))')
    L.append("")
    return L


def _render_base_block(cfg: dict) -> list[str]:
    L = []
    L.append("// COMMAND ----------")
    L.append("")
    L.append("// MAGIC %md ## Base")
    L.append("")
    L.append("// COMMAND ----------")
    L.append("")
    L.extend(_render_base_source(cfg))
    L.extend(_render_filters(cfg))
    L.extend(_render_groupby(cfg))
    L.extend(_render_segments(cfg))
    L.append("")
    return L


def _render_base_source(cfg: dict) -> list[str]:
    """Render `val base = <source>` plus the product tagging + union (BR + MX).

    v2 split model:
      - CC only  → collectionsCc tagged is_cc=1
      - LL only  → collectionsLl tagged is_ll=1
      - both/neither → unionByName(collectionsCc[is_cc=1,is_ll=0],
                                    collectionsLl[is_cc=0,is_ll=1])
        Tagging both sides keeps the union schema aligned and gives the
        is_cc / is_ll columns the segment logic relies on.

    The flag column names (`is_cc` / `is_ll` for both countries) come from
    COLUMN_NAMES.
    """
    iscc = _col(cfg, "iscc")
    isll = _col(cfg, "isll")
    cc, ll = _wants_cc_ll(cfg)
    if cc and ll:
        return [
            "val base = collectionsCc",
            f'  .withColumn("{iscc}", lit(1)).withColumn("{isll}", lit(0))',
            "  .unionByName(",
            f'    collectionsLl.withColumn("{iscc}", lit(0)).withColumn("{isll}", lit(1)),',
            "    allowMissingColumns = true",
            "  )",
        ]
    if cc:
        return [
            "val base = collectionsCc",
            f'  .withColumn("{iscc}", lit(1))',
        ]
    # ll only
    return [
        "val base = collectionsLl",
        f'  .withColumn("{isll}", lit(1))',
    ]


def render_scala(cfg: dict) -> str:
    """Render the full .scala source from a checklist config."""
    parts: list[str] = []
    parts.extend(_render_header(cfg))
    parts.extend(_render_datasets_block(cfg))
    if cfg.get("apply_forbidden_tags_filter"):
        parts.extend(_render_forbidden_tags_block(cfg))
    parts.extend(_render_base_block(cfg))
    parts.extend(_render_save_block(cfg))
    return "\n".join(parts)


def output_table_names(cfg: dict) -> list[str]:
    """Table names the notebook will `.save(...)` — 1 for single-save, N for
    multi-save. Thin alias over `multi_save_names` for callers that read more
    naturally as 'output tables' (e.g. the CSV export / runner)."""
    return multi_save_names(cfg)


def render_csv_export_cells(cfg: dict, volume_dir: str) -> str:
    """Render the CSV-export cells appended after the save block.

    For each output table, read it back and write a single header CSV under
    `<volume_dir>/<table>/`. `coalesce(1)` forces one part-file so the app can
    download a single CSV per table. Pure text — no Streamlit, no Spark — like
    the rest of `lib`.
    """
    vol = volume_dir.rstrip("/")
    L: list[str] = []
    for name in output_table_names(cfg):
        L.append("// COMMAND ----------")
        L.append("")
        L.append(f'table("{name}")')
        L.append("  .coalesce(1)")
        L.append('  .write.option("header", "true")')
        L.append('  .mode("overwrite")')
        L.append(f'  .csv("{vol}/{name}")')
        L.append("")
    return "\n".join(L)


def render_scala_with_csv_export(cfg: dict, volume_dir: str) -> str:
    """Full notebook source + CSV-export cells, for in-app execution.

    The bases are built and `.save(...)`d exactly as in the downloadable
    `.scala`; the only addition is the trailing export cells that write each
    table out as CSV to the given UC Volume directory.
    """
    return render_scala(cfg) + "\n\n" + render_csv_export_cells(cfg, volume_dir)
