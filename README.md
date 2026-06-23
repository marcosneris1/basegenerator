# Base Generator

A Streamlit app that generates Databricks **Scala notebooks** for building customer bases at Nubank, following the team's canonical patterns.

The user clicks through a checklist; each enabled option appends a specific Scala fragment to the rendered notebook. The output is a downloadable `.scala` file ready to import into Databricks via **File → Import**. Fully deterministic — no LLM, no description parsing.

## Files

| File | Role |
|------|------|
| `app.py` | Streamlit UI — sidebar + two-column checklist + live Scala preview |
| `lib.py` | Pure logic: constants, default configs, templates, validation, Scala renderer. Zero Streamlit imports, unit-testable |
| `requirements.txt` | Just `streamlit>=1.30` |

## How to run

### Local (step by step)

**1. Open the Terminal** (macOS: `Cmd + Space`, type "Terminal", press Enter).

**2. Go to the project folder.** This is wherever you keep the project files (`app.py`, `lib.py`, `requirements.txt`). For example, if the project lives in `Downloads/files`:

```bash
cd /Users/your_name/Downloads/files
```

> Tip: if your folder is somewhere else, drag the folder onto the Terminal window after typing `cd ` and the path fills in automatically.

**3. Activate the virtual environment.** The project keeps its dependencies in a `.venv` folder:

```bash
source .venv/bin/activate
```

You'll know it worked when `(.venv)` appears at the start of the prompt.

> First time on a new machine? Create the venv and install dependencies once, then activate it:
>
> ```bash
> python3 -m venv .venv
> source .venv/bin/activate
> pip install -r requirements.txt
> ```

**4. Start the app:**

```bash
streamlit run app.py
```

The browser opens automatically at `http://localhost:8501`. If it doesn't, copy the URL printed in the Terminal.

**5. To stop the app**, go back to the Terminal and press `Ctrl + C`.

Next time you only need steps 2 → 3 → 4.

### Databricks Apps (shared — no download/install for users)

Goal: host the code in Git, run it as a single Databricks App, and share only
the app URL. End users click a link — they never clone, download, or install
anything.

The repo already ships the two files the platform needs:

- `app.yaml` — start command (`streamlit run app.py`) + the CORS/XSRF env vars
  Streamlit needs behind the Databricks Apps proxy.
- `.databricksignore` — keeps `.venv/`, caches, and `.DS_Store` out of the sync.

**One-time setup**

1. **Put the code in Git.** Push `app.py`, `lib.py`, `requirements.txt`,
   `app.yaml`, and `.databricksignore` to a repo.
2. **Add a Git folder in the workspace:** **Workspace → Repos → Add repo**, point
   it at the repository. (This is what makes future updates a `git pull` instead
   of a re-upload.)
3. **Create the app:** **Compute → Apps → Create app → Custom**. Point it at the
   Git-folder path that contains `app.yaml`. Start it.
4. **Share it:** open the app → **Permissions** → grant the target group
   **Can use**. They open the app URL and use it directly.

**Updating later**

Pull the latest commit into the Git folder (or let CI sync it), then redeploy
the app. No user action required.

No secrets, no MCP, no external APIs — the app only renders text and touches
zero data, so the deployed app needs no data entitlements.

## What the app does

Pick **BR** or **MX** in the sidebar, optionally load a template, and walk through six sections in the main view. The Scala notebook updates live in the preview pane as you click. Hit **Download** to grab the `.scala` file.

### Sections

1. **🔍 Filters** — snapshot date, `days_late` range, customer type (person / company), `collection__end IS NULL`.
2. **🏷️ Derived flags** — `is_cc`, `is_ll`, and `cured`. If only one of CC / LL is ticked, the base is auto-filtered to that product. `cured` uses `collection__cured` on BR (already a column) and derives from `collection__end.isNotNull` on MX.
3. **🎯 Segmentation** — `lateness` (short / long with configurable cutoff), `segment` (`cc_only` / `ll_only` / `multi_debt`, requires both product flags), and **income segments** (`mass_market` / `super_core` / `high_income`, BR-only, joined from `dataset/br-segments-v5`).
   - **Split mode**: *Keep all segments*, *Filter to one segment*, or **Multi-save** — see below.
4. **🔒 Compliance** — applies the canonical `forbidden_tags` filter. BR uses `lib.FORBIDDEN_TAGS_BR` against `contract-customers/customers`; MX uses `lib.FORBIDDEN_TAGS_MX` against `sr-barriga/daily-snapshot` (lower-cased, substring match via `containsAny`).
5. **🧩 Enrichment** — optional BR-only joins.
   - **Roxinho only flag** — joins `nu-br/dataset/current-roxinho-customers`, builds a `roxinho` 0/1 column, then filters to `roxinho === 1`.
6. **💾 Output** — `select_columns` is a multiselect (lists only currently-available columns to prevent typos), plus a row-cap (**Number of rows** input).

### ⚙️ Advanced settings (expander)

- **Aggregation** — `groupBy("customer__id")` with `max(...)` on flags + `days_late` + `cured`. On MX the key is `("customer__id", "prototype")` and the helper used is `maximo(...)` instead of `max(...).as(...)`.

## Multi-save

When you have `segment` and/or `lateness` columns, the **Split mode** radio under Segmentation lets you produce one notebook with several saves instead of one. The sub-options are:

- *By segment* — pick any subset of `cc_only` / `ll_only` / `multi_debt`.
- *By lateness* — pick any subset of `short` / `long`.
- *By segment × lateness* — full cross-product of your chosen subsets.

A live preview shows the table names the run will produce, and each save block gets its own row cap (default seeded from the global value, edit per-table when, e.g., the *long* base needs 500k rows but the *short* one only needs 300k).

## Country handling

- **BR** — full coverage: `forbidden_tags`, `customers` re-attachment to carry `prototype` through a `groupBy`, Roxinho + income-segments enrichment, full validation set.
- **MX** — same split-source model as BR: the source is derived from the product flags using the new CC/LL daily datasets (union when both), each row tagged `is_cc` / `is_ll` by origin, `days_late` (`product__days_late`) and `cured` (`collection__cured`) read natively, and `prototype` carried through the `groupBy` natively. The MX-specific `forbidden_tags` substring filter pulls `customer__tags` from the SR Barriga daily snapshot. The Roxinho and income-segments enrichments are BR-only and auto-disabled in MX.

Switching country in the sidebar resets the checklist to that country's defaults.

### BR core datasets — v2 migration (effective July 2026)

The legacy combined snapshot `dataset/collections-daily-snapshot` (CC + LL) and the old per-product snapshots are being retired in **July 2026**. In v2, CC and LL live in **separate** `incremental-table` datasets, so the BR source is no longer a fixed table or a manual sidebar input — it is **derived from the product flags**:

| `is_cc` | `is_ll` | Source used |
|---------|---------|-------------|
| on | off | `…/collections-cc-portfolio-daily-snapshot-v2` |
| off | on | `…/collections-ll-portfolio-daily-snapshot-v2` |
| both / neither | | `unionByName(CC v2, LL v2, allowMissingColumns = true)` |

Because the combined snapshot has **no v2 replacement**, "both CC and LL" (or no product flag at all) renders a union of the two v2 datasets. The flags are no longer derived from `collection__origin_product`; instead each row is tagged `is_cc` / `is_ll` based on which dataset it came from. The sidebar shows the auto-resolved source for BR, and the old datasets are listed (marked obsolete) under "Obsolete datasets" for reference. The actual v2 source column names are centralized in `lib.COLUMN_NAMES` / the v2 path constants in `lib.py`, so a future schema rename is a one-line change there. Daily snapshots only for now (the v2 *current* variants exist as constants but aren't wired in).

### MX core datasets — split CC/LL migration

MX follows the **same model as BR**. The old combined `nu-mx/contract/sr-barriga/collections` source (where the product was inferred from `account__id` / `loan__id` presence and `days_late` / `cured` had to be derived) is superseded by **separate** CC and LL daily datasets, and the source is **derived from the product flags**:

| `is_cc` | `is_ll` | Source used |
|---------|---------|-------------|
| on | off | `nu-mx/incremental-table/collections-cc-portfolio-daily-snapshot` |
| off | on | `nu-mx/incremental-table/collections-ll-portfolio-daily-snapshot` |
| both / neither | | `unionByName(CC daily, LL daily, allowMissingColumns = true)` |

Each row is tagged `is_cc` / `is_ll` by which dataset it came from (no more `account__id` / `loan__id` derivation, no exclusivity filter). `days_late` and `cured` are read natively (`product__days_late`, `collection__cured`) like BR — the old `datediff` and `collection__end.isNotNull` derivations are gone. `prototype` is still native and kept in the `groupBy` key. The **only** thing the legacy SR Barriga daily snapshot is still used for is the `customer__tags` source behind the MX `forbidden_tags` substring filter. MX paths and column names live in `lib.py` (`MX_CC_DAILY` / `MX_LL_DAILY`, `COLUMN_NAMES["MX"]`).

## Templates

Five templates are available from the sidebar:

- **Eligibility Test (BR — full)** — canonical multi-debt eligibility base with `groupBy`, lateness + product segmentation, and `forbidden_tags`.
- **Homepage Research (BR — simple)** — minimal scaffold: PF + `forbidden_tags` only.
- **Eligibility Test (MX — full)** — MX equivalent of the BR eligibility template.
- **PDP Research (MX — with compliance)** — research sample with the MX substring-based `forbidden_tags` filter.
- **MX Collections (skeleton)** — starting point for ad-hoc MX bases.

Each template returns a fresh config copy from `default_config(country)` and overrides the relevant fields, so templates can't be mutated by accident.

## Validation

`lib.validate_config(cfg)` returns `(errors, warnings)`. Errors block code generation; warnings are advisory. Notable checks:

- Output name must be snake_case.
- Country ↔ dataset path mismatch.
- BR + person without `forbidden_tags` → warning.
- `days_late_low > days_late_high`.
- `segment_product` without both product flags.
- `filter_segment_only` without `segment_product`.
- `groupBy` with nothing to aggregate.
- Invalid snapshot date format.
- Per-save row caps validated individually in multi-save mode.
- `select_columns` must be non-empty; entries not in `available_select_columns(cfg)` flagged as typos.
- BR-only enrichments turned on for MX (Roxinho, income segments) → warning.
- `cured` flag combined with `collection__end IS NULL` filter → warning (the filter excludes everyone who is cured).
- Multi-save mode with empty subset selections → error.

## Architecture notes (worth preserving)

- **`lib.py` has zero Streamlit imports** — pure logic, importable and unit-testable from anywhere.
- **Config is a flat dict**, not a class — easier to serialize, persist, and edit live.
- **The renderer assembles fragments**, not a single string-template. Each section (`_render_filters`, `_render_flags`, `_render_groupby`, `_render_segments`, `_render_save_block`, `_render_one_save`, …) returns a list of lines, concatenated in `render_scala()`.
- **Dedup discipline on right-side lookups** — every dataset joined on `customer__id` (`customers`, `brSegments`, `roxinhoCustomers`) is deduped before the join so a downstream `groupBy("customer__id")` is never re-inflated.
- **Multi-save** is a thin wrapper around `_render_one_save(...)`, called once per `(segment, lateness)` combo with the appropriate `where` overrides and a table-name suffix.

## Limitations

- Window functions, multi-source joins, renegotiation logic, and cure events are out of scope — generate the skeleton, then edit manually.
- The `FORBIDDEN_TAGS_BR` / `FORBIDDEN_TAGS_MX` lists are hardcoded — drift risk over time. If this becomes shared tooling, move them to a versioned source.
- No automatic upload to the workspace — the app produces a downloadable `.scala`, then the user imports it manually via Databricks **File → Import**.
- All state lives in `st.session_state`; nothing is persisted between sessions.
