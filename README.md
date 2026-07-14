# Base Generator

A Streamlit app that generates Databricks **Scala notebooks** for building customer bases at Nubank, following the team's canonical patterns.

The user clicks through a checklist; each enabled option appends a specific Scala fragment to the rendered notebook. The output is a downloadable `.scala` file ready to import into Databricks via **File → Import**. Fully deterministic — no LLM, no description parsing.

**▶️ Open the hosted app:** https://base-generator-2093534396923660.aws.databricksapps.com (no install — see [Hosted app](#hosted-app-no-install)).

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

### Hosted app (no install)

The app is already running on Databricks. Nothing to download or install:

**👉 [base-generator](https://base-generator-2093534396923660.aws.databricksapps.com)**

How to open it:

1. Click the link above (or paste it into your browser):
   `https://base-generator-2093534396923660.aws.databricksapps.com`
2. If prompted, sign in with your **Nubank Databricks** account (the usual SSO).
3. Wait a few seconds for the app to load, then start clicking through the checklist.

> Don't have access? Ask the app owner to grant you **Can use** on the
> `base-generator` app in Databricks (**Compute → Apps → base-generator → Permissions**).

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

## Run on Databricks & export CSV (beta)

Beyond downloading the `.scala`, the app can **execute** the generated notebook
and hand you the full result as CSV. The **⚡ Run on Databricks & export CSV**
section (below the generated code) offers two modes:

### Mode 1 — Via Job (recommended for the deployed app)

Triggers a **pre-configured Databricks Job** that builds the base and writes the
CSV(s). It runs as the app's **service principal** using *resource grants*, so
**no on-behalf-of-user OAuth scopes are needed** (this side-steps the
`clusters`/`jobs` scope allow-list problem). Inputs:

- **Job ID** — the Job to trigger (attach it to the app as a resource). Default `109425859584826`.
- **UC Volume base directory** — e.g. `/Volumes/usr/basegenerator/base_generator_volume/`; each run writes to a **per-run subfolder** (`<user>_<timestamp>`), so simultaneous runs never collide.
- **Workspace folder** — where the per-run notebook is written before the Job runs it. Default `/Shared/base_generator/runs` (a workspace-object path — do **not** prefix it with `/Workspace`).
- **Timeout** — how long to wait for the Job run.

What happens on **Run via Job & build CSV**:

1. The app renders the notebook (code + a CSV-export cell per table, wired to the per-run Volume subfolder) and imports it to the workspace folder.
2. The app calls `run_now` on the Job, passing the notebook path as a parameter.
3. The Job's task (`job_runner.py`) runs the generated notebook via `dbutils.notebook.run` on its cluster; each table is written as a single header CSV (`coalesce(1)`) to the Volume.
4. The app polls the run to completion, then downloads each CSV and offers a browser **Download** button.

**Setup (one-time):**

- **Job:** create a Databricks Job whose single **notebook task** points at `job_runner.py` (from this repo, ideally Git-sourced). Give the Job's **run-as identity** access to the source datasets and **WRITE VOLUME** on the Volume. Set `max_concurrent_runs` to 10–20 so multiple users can run at once.
- **App resources:** in the app settings, attach the **Job** and the **UC Volume** as *resources* and grant the service principal **CAN MANAGE RUN** on the Job and **READ/WRITE VOLUME** on the Volume. The service principal also needs **write** on the workspace folder (`/Shared/base_generator/runs`), and the Job's **run-as identity** needs **read** on it (so it can run the per-run notebook the app wrote).
- Optionally override the defaults with env vars `BASE_GENERATOR_JOB_ID`, `BASE_GENERATOR_VOLUME`, `BASE_GENERATOR_NOTEBOOK_DIR`.

### Mode 2 — Interactive (existing cluster)

Runs the generated Scala **cell-by-cell** on a running cluster via execution
contexts (the same mechanism a notebook cell uses). Inputs: **cluster ID/name**
+ **UC Volume output directory**. Because it runs interactively rather than as a
Jobs run, it works on **interactive-only clusters** (jobs workload disabled); the
cluster must be **running**. Steps:

1. The generated notebook is split into cells; any `%run` helper notebook is **inlined** (its source is fetched and its cells run first, so `datasets()`, `.save()`, `maximo`, … are defined).
2. An execution context is opened on the cluster and each cell runs in order (state persists across cells).
3. Each output table is written as a single header CSV (`coalesce(1)`) to the Volume, then downloaded.

When deployed, this mode runs **on behalf of the logged-in user** (forwarded
`x-forwarded-access-token`) and therefore needs the workspace's *OAuth scopes for
apps* allow-list to include compute/command-execution/files scopes — which is why
**Mode 1 (Via Job) is preferred** where those scopes aren't available.

### How it works in code

- `lib.render_scala_with_csv_export(cfg, volume_dir)` / `lib.render_csv_export_cells(...)` — pure renderers that append the export cells.
- `job_runner.py` — the generic Job task notebook that runs the app-generated notebook by path.
- `runner.py` — UI-agnostic execution layer with `run_via_job(...)` (Mode 1) and `run_interactive(...)` (Mode 2). The Databricks SDK is imported lazily, so the rest of the app still works without it.

**Auth & permissions.**

- **Local dev:** the SDK uses your CLI profile (`Marcos Neris`, override with `DATABRICKS_CONFIG_PROFILE`). The profile is pinned to avoid the slow default-auth provider probing.
- **Deployed app, Mode 1 (Via Job):** runs as the **service principal** via resource grants — no user consent/scopes needed.
- **Deployed app, Mode 2 (Interactive):** runs **on behalf of the logged-in user**; requires enabling User Authorization + scopes (admin, one-time) and a full stop/start of the app.

**⚠️ PII / compliance.** This runs against production data and exports a full
base (personal data — CPF/CNPJ, tags) to CSV. Only run authorized, reviewed
bases. For very large bases, `coalesce(1)` forces a single file through one
task — consider leaving it partitioned and downloading parts instead.

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
- Manual path: the app produces a downloadable `.scala` to import via Databricks **File → Import**. The **Run on Databricks (beta)** section can instead run it for you interactively and export CSV (see above).
- In-app execution is **synchronous and client-driven** — the app runs the cells one by one and blocks until they finish (with per-cell progress). Closing the app stops the run; there's no background queue or run history yet.
- Interactive execution needs a **running** cluster; it won't start a stopped one.
- All state lives in `st.session_state`; nothing is persisted between sessions.
