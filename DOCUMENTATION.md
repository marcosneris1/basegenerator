# Base Generator — Project Documentation

> Full reference for the Base Generator project. For a quick-start overview, see [README.md](README.md).

---

## Table of contents

1. [Context & purpose](#1-context--purpose)
2. [Project structure](#2-project-structure)
3. [Setup & running](#3-setup--running)
4. [Using the app](#4-using-the-app)
5. [Country handling (BR vs MX)](#5-country-handling-br-vs-mx)
6. [Templates](#6-templates)
7. [Multi-save](#7-multi-save)
8. [Configuration reference](#8-configuration-reference)
9. [Architecture](#9-architecture)
10. [Generated notebook anatomy](#10-generated-notebook-anatomy)
11. [Validation rules](#11-validation-rules)
12. [Key invariants (read before changing code)](#12-key-invariants-read-before-changing-code)
13. [Extending the project](#13-extending-the-project)
14. [Limitations & out of scope](#14-limitations--out-of-scope)

---

## 1. Context & purpose

**Base Generator** is a Streamlit app that generates Databricks **Scala notebooks** for building customer bases at Nubank, following the team's canonical notebook patterns (collections / debt resolution bases).

The core idea: instead of hand-writing (or LLM-generating) Scala, the user clicks through a **checklist UI**. Each enabled option appends a specific, deterministic Scala fragment to a live-rendered notebook. The output is downloaded as a `.scala` file and imported into Databricks via **File → Import**.

Design principles:

- **Fully deterministic** — no LLM, no free-text parsing. The same checklist always produces the same Scala.
- **Safe by construction** — the column picker only lists columns that actually exist at that point in the pipeline; validation blocks configs that would produce broken Scala.
- **Reviewable output** — the generator builds the skeleton; users are expected to review the code with a teammate before running on production data.

---

## 2. Project structure

| File | Role |
|------|------|
| `app.py` | Streamlit UI — sidebar (country, names, templates) + two-column checklist + advanced-settings expander + validation panel + live Scala preview & download + **Run on Databricks** section. No business logic. |
| `lib.py` | **Pure logic**: constants, default configs, templates, validation, Scala renderer (incl. CSV-export cells). **Zero Streamlit imports** — importable and unit-testable from anywhere. |
| `runner.py` | **Execution layer** (beta): two modes — `run_via_job(...)` triggers a pre-configured Databricks **Job** as the app's service principal (writes a per-run notebook, `run_now`, polls, downloads CSV from a UC Volume); `run_interactive(...)` runs the Scala cell-by-cell on an existing cluster via the Command Execution API (inlining `%run` helpers). UI-agnostic; Databricks SDK imported lazily. |
| `job_runner.py` | Generic **Job task notebook**: reads the app-generated notebook inline (`source_b64` widget), writes it to `/Shared/base_generator/runs/<run_id>` (`notebook_dir` widget; falls back to the run-as user's home), and runs it via `dbutils.notebook.run` (so the creator == the runner — no cross-identity ACLs). Point the Job's notebook task at this file. |
| `requirements.txt` | `streamlit>=1.30`, `databricks-sdk>=0.30`. |
| `README.md` | Quick-start overview. |
| `.venv/` | Local virtual environment (not versioned). |

The separation matters: everything that decides *what Scala to emit* lives in `lib.py`; `app.py` only reads/writes a config dict and calls `lib` functions.

---

## 3. Setup & running

### Local

```bash
cd /Users/your_name/Downloads/files     # or wherever the project lives

# First time only — create the venv and install dependencies:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Every time:
source .venv/bin/activate
streamlit run app.py
```

The browser opens at `http://localhost:8501`. Stop with `Ctrl + C` in the Terminal.

### Hosted app (no install)

The app is deployed on Databricks Apps — most users never run it locally, they just open the link:

**https://base-generator-2093534396923660.aws.databricksapps.com**

How to open it:

1. Click the link (or paste it into your browser).
2. If prompted, sign in with your **Nubank Databricks** account (SSO).
3. Wait for the app to load, then use the checklist.

Need access? The app owner grants **Can use** under **Compute → Apps → base-generator → Permissions**.

---

## 4. Using the app

Pick **BR** or **MX** in the sidebar, optionally load a template, then walk through the sections. The Scala preview updates live as you click. When validation passes, hit **Download .scala**.

### Sidebar

- **Country** — BR / MX radio. Switching resets the checklist to that country's defaults.
- **Base name** — the output table name (snake_case).
- **Source (auto)** — the source dataset is derived from the product flags (CC / LL / union); there's no manual primary-dataset, `val` alias, or `imports_uc` path field in the UI (those are fixed defaults).
- **Templates** — apply one of five pre-filled checklists.
- **Known datasets** — quick reference of canonical dataset paths per country.
- **Obsolete datasets** — a reference list of retired collections datasets per country (the generator already emits the v2 replacements).

### Main sections

1. **🔍 Filters** — narrow which rows enter the base:
   - Snapshot date → `.where($"date" === "YYYY-MM-DD")`
   - `days_late` range (min/max)
   - Customer type (person / company)
   - Open collections only → `.where($"collection__end".isNull)` (MX default: on)
   - **Cured collections only** → `.where($"collection__cured" === 1)`. Both BR and MX read the native `collection__cured` 0/1 column.
2. **💜 Nubank customer tier** (BR-only) — one filter per tier. Each joins the tier's `customer__id` lookup, builds a 1/0 column, and keeps only matched rows (`.where($"<col>" === 1)`). Ticking more than one ANDs them. Tiers (see `CUSTOMER_TIERS` in `lib.py`):
   - **Roxinho** → `nu-br/dataset/current-roxinho-customers` (col `roxinho`)
   - **Ultravioleta (UV)** → `nu-br/dataset/current-uv-customers` (col `uv`)
   - **Nubank+** → `nu-br/dataset/current-nu-plus-customers` (col `nu_plus`)
   - **Under 18 (U18)** → `nu-br/dataset/current-underage-customers-ids` (col `u18`)
3. **🏷️ Derived flags** — 1/0 product columns:
   - `is_cc` (credit card) and `is_ll` (lending). **If only one is ticked, the base is auto-filtered to that product.**
4. **🎯 Segmentation**:
   - **lateness** — `short` / `long` with a configurable day cutoff.
   - **segment** — `cc_only` / `ll_only` / `multi_debt`; requires *both* product flags.
   - **Income segments** (BR-only) — joins `dataset/br-segments-v5` to attach `income_segments` (`mass_market` / `super_core` / `high_income`). Tick a strict subset to filter; tick all three to just attach the column.
   - **Split mode** — *Keep all segments*, *Filter to one segment*, or *Multi-save* (see [§7](#7-multi-save)).
5. **🔒 Compliance** — the canonical `forbidden_tags` filter, country-specific (see [§5](#5-country-handling-br-vs-mx)).
6. **💾 Output**:
   - **Columns to keep** — a multiselect listing *only currently-available columns* (prevents typos / "column not found" at runtime).
   - **Base sample size** — row cap via `.limit(n)`. In multi-save mode this becomes one input per resulting table.

### ⚙️ Advanced settings (expander)

- **Aggregation** — `groupBy("customer__id")` with `max(...)` on the flags and `days_late`. On MX the key is `("customer__id", "prototype")` and the helper is `maximo(...)` instead of `max(...).as(...)`.

### Validation panel

Below the checklist, `validate_config` results are shown live. **Errors block code generation** (the preview/download disappear); warnings are advisory.

### Importing into Databricks

1. Download the `.scala` file.
2. Databricks workspace: **File → Import → Drop file or click to browse**.
3. Attach to a cluster and run cell by cell.
4. **Always review the code before running on production data.**

---

## 5. Country handling (BR vs MX)

Switching country in the sidebar **resets the checklist** to that country's defaults (`default_config(country)`).

| Aspect | BR | MX |
|---|---|---|
| Coverage | Full | Full (same split-source model as BR) |
| Source | Derived from flags: `collectionsCc` / `collectionsLl` v2, union when both | Derived from flags: `collectionsCc` / `collectionsLl` daily, union when both |
| Flag columns | `is_cc` / `is_ll`, tagged at source by which dataset a row came from | `is_cc` / `is_ll`, tagged at source by which dataset a row came from |
| `days_late` | Native `product__days_late` (v2 rename) | Native `product__days_late` (v2 rename) |
| Cured filter | `.where($"collection__cured" === 1)` (native column) | `.where($"collection__cured" === 1)` (native column) |
| Open-collections filter | Off by default | **On by default** (MX historically scoped to open collections) |
| `prototype` | Enriched via inner join from `contract-customers/customers` | Native on the new datasets; also a groupBy key |
| GroupBy | `groupBy("customer__id")`, `max(c).as(c)` | `groupBy("customer__id", "prototype")`, `maximo(c)` |
| Forbidden tags | `FORBIDDEN_TAGS_BR` (27 tags), **exact match** via `containsAny` on exploded `customer__tags` from `contract-customers/customers` | `FORBIDDEN_TAGS_MX` (36 substrings), **case-insensitive substring match** via `instr` against `etl.mx__dataset.collections_daily_snapshot_sr_barriga` (`spark.table`, not `datasets()`) |
| Nubank customer tiers | ✅ Roxinho / UV / Nubank+ / U18 | ❌ auto-disabled (BR-only datasets) |
| Income segments | ✅ from `dataset/br-segments-v5` | ❌ auto-disabled (BR-only dataset) |
| Default `days_late` max / lateness cutoff | 365 / 65 | 60 / 60 |

---

## 6. Templates

Five templates are available in the sidebar. Each one calls `default_config(country)` and overrides fields — templates always return a **fresh copy**, so they can't be mutated by accident.

| Template | Country | What it sets up |
|---|---|---|
| **Eligibility Test (BR — full)** | BR | Canonical multi-debt eligibility base: snapshot date, days_late 5–365, person, both flags, `groupBy`, lateness + product segmentation, `forbidden_tags`, 100k rows. |
| **Homepage Research (BR — simple)** | BR | Minimal scaffold: person filter + `forbidden_tags`, 50k rows. |
| **Eligibility Test (MX — full)** | MX | MX equivalent of the BR eligibility base: open collections, days_late 5–60, both flags, `groupBy`, both segmentations. **No compliance step** (matching the team's eligibility notebook). |
| **PDP Research (MX — with compliance)** | MX | Research sample with the MX substring-based `forbidden_tags` filter. |
| **MX Collections (skeleton)** | MX | Minimal MX starting point: open collections only. |

---

## 7. Multi-save

When `segment` and/or `lateness` segmentation is on, the **Split mode** radio offers *Multi-save*: one notebook with several `save` blocks instead of one.

Sub-modes (availability depends on which segmentations are enabled):

- **By segment** — pick any subset of `cc_only` / `ll_only` / `multi_debt`.
- **By lateness** — pick any subset of `short` / `long`.
- **By segment × lateness** — full cross-product of the chosen subsets.

Behavior:

- The UI shows a **live preview of the table names** (`<output_name>_<segment>_<lateness>`).
- Each save block gets **its own row cap**, seeded from the global `limit_value` and stored in the `limit_values` dict keyed by `combo_key(seg, late)` (e.g. `"multi_debt_long"`). Missing keys fall back to `limit_value`.
- The shared `val base` (filters, flags, groupBy, segments) is declared **once**; each save chain re-applies only the combo's `where` overrides.
- Selecting a combination that yields only one table triggers a warning (it's equivalent to a single filtered save).

---

## 8. Configuration reference

The entire app state is a **flat dict** stored at `st.session_state.config`, produced by `default_config(country)`. All keys:

| Key | Type | Meaning |
|---|---|---|
| `country` | `"BR" \| "MX"` | Drives column names, datasets, defaults, and feature availability. |
| `output_name` | str | Saved table name (snake_case). |
| `imports_uc_path` | str | Path passed to `%run` (helpers notebook). |
| `primary_dataset_path` | str | Nominal only — the real source is derived from the product flags (CC / LL / union) for both BR and MX. Kept for config shape. |
| `primary_dataset_alias` | str | Nominal only (see above). |
| `filter_snapshot_date` / `snapshot_date` | bool / str | `.where($"date" === ...)` filter (ISO date). |
| `filter_days_late_range` / `days_late_low` / `days_late_high` | bool / int / int | Min/max overdue-days filter. |
| `filter_customer_type` / `customer_type` | bool / `"person" \| "company"` | Customer-type filter. |
| `filter_collection_end_null` | bool | Keep only open collections. |
| `filter_cured_only` | bool | Keep only cured collections (`where collection__cured === 1`). |
| `flag_roxinho` / `flag_uv` / `flag_nu_plus` / `flag_u18` | bool | BR-only Nubank customer tier filters (join + `where <col> === 1`). One per tier in `CUSTOMER_TIERS`. |
| `flag_is_cc` / `flag_is_ll` | bool | Product flags. One alone also filters the base to that product. |
| `segment_income` / `income_segment_values` | bool / list[str] | BR-only income segments; subset of `["mass_market", "super_core", "high_income"]`. Strict subset → filter; all three → attach only. |
| `groupby_customer_id` | bool | Aggregation to one row per customer (per customer+prototype on MX). |
| `segment_lateness` / `lateness_cutoff` | bool / int | `lateness` short/long column with cutoff in days. |
| `segment_product` | bool | `segment` cc_only/ll_only/multi_debt column. Requires both flags. |
| `filter_segment_only` / `segment_only_value` | bool / str | Single-segment filter mode (mutually exclusive with multi-save). |
| `multi_save_mode` | `"none" \| "segment" \| "lateness" \| "segment_lateness"` | Multi-save dimension(s). |
| `multi_save_segments` / `multi_save_lateness` | list[str] | Subsets to split on. |
| `apply_forbidden_tags_filter` | bool | Country-specific compliance filter. |
| `select_columns` | list[str] | Final `.select(...)` columns. Must be non-empty. |
| `limit_enabled` / `limit_value` | bool / int | Global row cap. |
| `limit_values` | dict[str, int] | Per-combo row-cap overrides (multi-save), keyed by `combo_key(seg, late)`. |

### Public API of `lib.py`

| Function | Purpose |
|---|---|
| `default_config(country)` | Fresh checklist config for a country. |
| `template_names()` / `get_template(name)` / `TEMPLATES` | Pre-filled configs (each call returns a fresh copy). |
| `validate_config(cfg)` | → `(errors, warnings)`. Errors block generation. |
| `render_scala(cfg)` | → full `.scala` source string. |
| `available_select_columns(cfg)` | Columns the user may pick in Output, given the current flags/segments/groupBy. |
| `forbidden_tags_for(country)` | The country's forbidden-tags list. |
| `multi_save_combos(cfg)` | List of `(segment, lateness)` tuples the multi-save will produce (`None` = no filter on that dimension). |
| `multi_save_names(cfg)` | Table names that will be produced (1 for single-save, N for multi-save). |
| `combo_key(seg, late)` | Stable string key for a combo — used in `limit_values` and as Streamlit widget keys. |

Key constants: `FORBIDDEN_TAGS_BR`, `FORBIDDEN_TAGS_MX`, `KNOWN_DATASETS`, `COLUMN_NAMES`, `ALL_SEGMENTS`, `ALL_LATENESS`, `ALL_INCOME_SEGMENTS`, `INCOME_SEGMENT_LABELS`, `MX_DAILY_SNAPSHOT_TABLE`, `ROXINHO_DATASET_BR`, `BR_SEGMENTS_DATASET`, `BR_CC_DAILY_V2`, `BR_LL_DAILY_V2`, `MX_CC_DAILY`, `MX_LL_DAILY`, `LEGACY_BR_COLLECTIONS`, `LEGACY_MX_COLLECTIONS`.

---

## 9. Architecture

```
app.py (Streamlit)                       lib.py (pure logic)
┌──────────────────────────┐             ┌──────────────────────────────┐
│ st.session_state.config  │──reads .────│ default_config / TEMPLATES   │
│   (flat dict)            │             │                              │
│                          │──validates──│ validate_config → (err, wrn) │
│ checkbox / radio /       │             │                              │
│ multiselect widgets      │──renders────│ render_scala                 │
│ write straight into cfg  │             │   ├ _render_header           │
│                          │             │   ├ _render_datasets_block   │
│ live preview + download  │             │   ├ _render_forbidden_tags…  │
└──────────────────────────┘             │   ├ _render_base_block       │
                                         │   │   ├ _render_filters      │
                                         │   │   ├ _render_groupby      │
                                         │   │   └ _render_segments     │
                                         │   └ _render_save_block       │
                                         │       └ _render_one_save ×N  │
                                         └──────────────────────────────┘
```

Design decisions worth preserving:

- **`lib.py` has zero Streamlit imports.** Pure functions over a dict → trivially unit-testable.
- **Config is a flat dict, not a class.** Easier to serialize, persist, mutate from widgets, and diff.
- **The renderer assembles line-list fragments**, not one giant string template. Each `_render_*` function returns a `list[str]` of Scala lines; `render_scala` concatenates them with `"\n"`. This makes each feature an isolated, composable block.
- **Column-name indirection** — the UI uses logical keys (`iscc`, `isll`, `days_late`); `COLUMN_NAMES[country]` maps them to the real column names (`is_cc` / `is_ll`; `days_late` → `product__days_late` in v2).
- **Segment filtering happens at save time**, not in the `val base` chain (`_render_segments` only adds the columns). This keeps `base` reusable across all multi-save variants.
- **`_render_one_save` is the per-save core.** Single-save calls it once; multi-save is a thin wrapper calling it once per combo with `segment_override` / `lateness_override` and a table-name suffix.

---

## 10. Generated notebook anatomy

A generated `.scala` file is a Databricks notebook source with these sections (cells separated by `// COMMAND ----------`):

1. **Header** — `%run <imports_uc_path>`, a country-specific markdown title (including a compliance heads-up on MX when the filter is off), and `spark.conf.set("spark.databricks.remoteFiltering.blockSelfJoins", "false")`.
2. **Datasets** — `val collectionsCc` / `val collectionsLl = datasets("<path>")` (the product dataset(s) the base needs, BR + MX) plus any conditionally-needed lookups:
   - `val customers` (BR: compliance and/or `prototype` enrichment)
   - `val srBarrigaDailySnapshot` (MX compliance, via `spark.table`)
   - one `val <tier>Customers` per enabled Nubank customer tier (`.select($"customer__id").distinct.withColumn("<col>", lit(1))`) — e.g. `roxinhoCustomers`, `uvCustomers`, `nuPlusCustomers`, `u18Customers`
   - `val latestDate` + `val brSegments` (income segments: `income_month >= latestDate`, select, `dropDuplicates`)
3. **Forbidden tags filter** (if enabled) — builds `val forbiddenTagsCustomers`:
   - BR: explode `customer__tags`, `containsAny` exact match, `groupBy` + `maximo`, keep flagged.
   - MX: lowercase tags, `EXISTS(..., instr(...) > 0)` substring match, distinct `customer__id`s.
4. **Base** — `val base` built from the product dataset(s): a single tagged dataset, or `unionByName(collectionsCc[is_cc=1,is_ll=0], collectionsLl[is_cc=0,is_ll=1])` when both flags (or neither) are set. Then the chain: filters (including the cured-only `where`) → groupBy/agg → segment columns. Product flags are tagged at the source, so there's no separate flag-derivation or single-flag exclusivity step.
5. **Save** (×1 or ×N) — per save: `base` → `leftanti` join on forbidden tags → enrichment joins → customer-tier / income filters → lateness/segment `where` overrides → `.select(...)` → `.limit(n)` → `.save("name")`, followed by a `table("name").d` display cell.

---

## 11. Validation rules

`validate_config(cfg)` returns `(errors, warnings)`. **Errors block generation**; warnings are advisory.

**Errors:**

- `days_late_low > days_late_high` (or missing values while the filter is on).
- `segment_product` without both `flag_is_cc` and `flag_is_ll`.
- `filter_segment_only` without `segment_product`, or an invalid `segment_only_value`.
- `filter_segment_only` and multi-save both on (mutually exclusive).
- Multi-save mode requiring a segmentation that isn't enabled, or with an empty subset selection.
- Invalid snapshot-date format (must be `YYYY-MM-DD`).
- Invalid global sample size; invalid per-table sample size (multi-save).
- Income segments on with zero values selected (base would be empty).
- Empty `select_columns`.

**Warnings:**

- Output name not snake_case.
- Person-type base without `forbidden_tags` (usually required for outbound/research; eligibility tests typically skip it).
- BR `groupBy` with nothing to aggregate (`.agg()` will be skipped).
- Sample size > 1,000,000 (globally or per table).
- `cured collections only` combined with the open-collections filter (cured collections have usually ended, so the base is likely empty).
- BR-only features (Nubank customer tiers, income segments) enabled while country is MX.
- Multi-save producing only one table (equivalent to a single filtered save).
- Selected columns not in `available_select_columns(cfg)` (likely typos).

---

## 12. Key invariants (read before changing code)

1. **`lib.py` must stay Streamlit-free.** All UI state flows through the flat config dict.
2. **Dedup discipline on right-side lookups.** Every dataset joined on `customer__id` must be deduped *before* the join, so a downstream (or upstream) `groupBy("customer__id")` is never re-inflated:
   - `customers` enrichment → `.dropDuplicates(Seq("customer__id"))`. The dataset is contract-level with **no date/recency column**; enriched columns like `prototype` are stable customer attributes, identical across a customer's rows, so deduping is safe.
   - `brSegments` → recency filter `income_month >= latestDate` (where `latestDate = collectionsCc.agg(max("date")).collect()(0).getDate(0)`) **plus** `.dropDuplicates(Seq("customer__id"))` as the safety net.
   - Nubank customer tier lookups (`roxinhoCustomers`, `uvCustomers`, `nuPlusCustomers`, `u18Customers`) → `.select($"customer__id").distinct` before adding the constant `<col> = 1`.
3. **Single-flag source selection** (only `is_cc` → read the CC dataset; only `is_ll` → read the LL dataset; both or neither → union of CC + LL) is intentional behavior, surfaced in the UI as the auto-resolved source caption.
4. **Save-time vs base-time filtering.** Segment/lateness *columns* go on `val base`; segment/lateness *filters* go in `_render_one_save`. Don't move them.
5. **`available_select_columns` must mirror the renderer.** If you add a column-producing feature, update both `available_select_columns` (UI options) and `_columns_produced_by_base` (enrichment detection), or the Output multiselect and the customers-join logic will drift from reality.
6. **Templates return fresh copies** — they're factory functions over `default_config`, never shared dicts.
7. **`combo_key` shape** is the table-name suffix without the leading underscore. It's used both as a `limit_values` dict key and as a Streamlit widget key — keep it stable.

---

## 13. Extending the project

### Adding a new checkbox feature (typical flow)

1. **`lib.py`**: add the config key(s) with a default in `default_config`.
2. **`lib.py`**: emit the Scala in the right `_render_*` function (or add a new fragment function and wire it into `render_scala` / `_render_one_save`).
3. **`lib.py`**: if the feature produces a column, update `available_select_columns` and (if it exists on `val base`) `_columns_produced_by_base`.
4. **`lib.py`**: add validation (errors for broken combos, warnings for suspicious ones).
5. **`lib.py`**: if the feature needs a lookup dataset, add a `_needs_*_dataset` helper, declare the `val` in `_render_datasets_block`, and **dedup it on the join key** (invariant #2).
6. **`app.py`**: add the widget via the keyed `w_*` helpers (source of truth is `session_state`, seeded from `cfg`). Disable + force-off for unsupported countries (see the Nubank customer tier pattern). For a whole new customer tier, just add an entry to `CUSTOMER_TIERS` in `lib.py` — the renderer, validation, columns and UI all iterate over it.
7. Optionally update a template.

### Adding a new template

Write a `_my_template()` factory that starts from `default_config(country)`, `update`s the relevant keys, and register it in `TEMPLATES`.

### Adding a new country

Add entries to `COLUMN_NAMES`, `KNOWN_DATASETS`, a forbidden-tags list + `forbidden_tags_for` branch, a branch in `default_config`, and country-aware branches in the renderer functions that differ (`_render_filters`, `_render_groupby`, `_render_forbidden_tags_block`, header).

---

## 14. Limitations & out of scope

- **Window functions, multi-source joins, renegotiation logic, and cure events** are out of scope. Generate the skeleton, then edit the Scala manually.
- **`FORBIDDEN_TAGS_BR` / `FORBIDDEN_TAGS_MX` are hardcoded** — drift risk over time. If this becomes shared tooling, move them to a versioned source of truth.
- **Download-only** — no automatic upload to the workspace. The user imports the `.scala` manually via Databricks **File → Import**.
- **No persistence** — all state lives in `st.session_state`; nothing survives a browser refresh or new session.
- **MX uses the same split-source model as BR** (CC/LL daily datasets, union when both, native `product__days_late` and `collection__cured`, `prototype` carried through the `groupBy`). BR-only features (Nubank customer tiers, income segments) remain auto-disabled in MX; the SR Barriga daily snapshot is used only as the `customer__tags` source for MX compliance.
