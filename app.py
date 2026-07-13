"""Base Generator — Streamlit app (English).

A friendly checklist UI that turns clicks into a Databricks Scala notebook.
No SQL or Scala knowledge required to use it — but you should still review
the generated code with a teammate before running it on production data.

Run:
    pip install streamlit
    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from lib import (
    ALL_INCOME_SEGMENTS,
    ALL_LATENESS,
    ALL_SEGMENTS,
    INCOME_SEGMENT_LABELS,
    KNOWN_DATASETS,
    LEGACY_BR_COLLECTIONS,
    LEGACY_MX_COLLECTIONS,
    available_select_columns,
    source_description,
    combo_key,
    default_config,
    forbidden_tags_for,
    get_template,
    multi_save_combos,
    multi_save_names,
    output_table_names,
    render_scala,
    render_scala_with_csv_export,
    summarize_config,
    template_names,
    validate_config,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Base Generator",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "config" not in st.session_state:
    st.session_state.config = default_config("BR")


# IMPORTANT: the checklist widgets below intentionally do NOT pass a Streamlit
# `key`. The single source of truth is `st.session_state.config` (aka `cfg`),
# and every widget is driven by `value=`/`index=` read from it. If a widget also
# had a `key`, Streamlit would keep a second copy of the value under that key and
# prefer it over `value=`/`index=` on rerun — which makes the widget drift out of
# sync with `cfg` (a click registers, then snaps back to the stale stored value).
# Keep these widgets key-less so `cfg` always wins.
def reset_to_country(country: str):
    st.session_state.config = default_config(country)


def apply_template(name: str):
    st.session_state.config = get_template(name)


cfg = st.session_state.config


# ---------------------------------------------------------------------------
# Sidebar — settings & templates
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚙️ Settings")

    new_country = st.radio(
        "Country",
        ["BR", "MX"],
        index=["BR", "MX"].index(cfg["country"]),
        help="Switching country resets the checklist to the new country's defaults.",
    )
    if new_country != cfg["country"]:
        reset_to_country(new_country)
        st.rerun()

    cfg["output_name"] = st.text_input(
        "Base name (snake_case)",
        value=cfg["output_name"],
        help="The name of the table that will be saved. Use lowercase letters, numbers, and underscores.",
    )
    # v2 (BR + MX): the source is chosen automatically from the product flags
    # (CC / LL / union), so there's no manual path to enter.
    st.caption(f"📦 {cfg['country']} source (auto): **{source_description(cfg)}**")

    st.divider()
    st.subheader("📋 Templates")
    st.caption("Start from a ready-made checklist instead of clicking from scratch.")
    selected_template = st.selectbox(
        "Pick a template",
        ["(none)"] + template_names(),
    )
    if selected_template != "(none)":
        st.caption("This template will tick:")
        for bullet in summarize_config(get_template(selected_template)):
            st.markdown(f"- {bullet}")
        if st.button("Apply template", use_container_width=True):
            apply_template(selected_template)
            st.rerun()

    st.divider()
    if st.button("🔄 Reset checklist", use_container_width=True):
        reset_to_country(cfg["country"])
        st.rerun()

    with st.expander(f"📚 Known datasets for {cfg['country']}"):
        for n, p in KNOWN_DATASETS.get(cfg["country"], {}).items():
            st.code(f"{n}: {p}", language="text")

    if cfg["country"] == "BR":
        with st.expander("⚠️ Obsolete datasets (retired Jul 2026)"):
            st.caption(
                "These BR collections datasets are being retired in July 2026. "
                "The generator already emits the v2 replacements. The combined "
                "`collections-daily-snapshot` has no v2 — when you need both CC "
                "and LL, the notebook unions the two v2 datasets instead."
            )
            for n, p in LEGACY_BR_COLLECTIONS.items():
                st.code(f"{n}: {p}", language="text")
    else:
        with st.expander("⚠️ Obsolete datasets"):
            st.caption(
                "MX now uses separate CC and LL daily datasets, chosen from the "
                "product flags. The old combined `sr-barriga/collections` source is "
                "superseded — when you need both CC and LL, the notebook unions the "
                "two datasets. The SR Barriga daily snapshot is still used only as "
                "the customer-tags source for the forbidden_tags filter."
            )
            for n, p in LEGACY_MX_COLLECTIONS.items():
                st.code(f"{n}: {p}", language="text")


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🛠️ Base Generator")
st.caption(
    "Build a base by ticking checkboxes. Each checkbox adds a chunk of code to the "
    "Scala notebook below — which updates live as you click. When you're done, "
    "download the file and import it into Databricks."
)


# ---------------------------------------------------------------------------
# Checklist sections
# ---------------------------------------------------------------------------

col_left, col_right = st.columns([1, 1])

with col_left:
    # ---------- Filters ----------
    with st.container(border=True):
        st.subheader("🔍 Filters")
        st.caption("Narrow down which rows go into your base.")

        cfg["filter_snapshot_date"] = st.checkbox(
            "Filter by snapshot date",
            value=cfg["filter_snapshot_date"],
            help='Keeps only rows from a specific day. Adds: .where($"date" === "<date>")',
        )
        if cfg["filter_snapshot_date"]:
            cfg["snapshot_date"] = st.text_input(
                "Date (YYYY-MM-DD)",
                value=cfg["snapshot_date"],
            )

        cfg["filter_days_late_range"] = st.checkbox(
            "Filter by days_late range",
            value=cfg["filter_days_late_range"],
            help="Keep customers whose collection is overdue between a min and max number of days.",
        )
        if cfg["filter_days_late_range"]:
            c1, c2 = st.columns(2)
            with c1:
                cfg["days_late_low"] = st.number_input(
                    "Min days late",
                    min_value=0,
                    value=cfg["days_late_low"],
                )
            with c2:
                cfg["days_late_high"] = st.number_input(
                    "Max days late",
                    min_value=0,
                    value=cfg["days_late_high"],
                )

        cfg["filter_customer_type"] = st.checkbox(
            "Filter by customer type",
            value=cfg["filter_customer_type"],
            help="Keep only individuals (person) or businesses (company).",
        )
        if cfg["filter_customer_type"]:
            cfg["customer_type"] = st.radio(
                "Type",
                ["person", "company"],
                index=["person", "company"].index(cfg["customer_type"]),
                horizontal=True,
            )

        cfg["filter_collection_end_null"] = st.checkbox(
            "Only open collections (collection__end is null)",
            value=cfg["filter_collection_end_null"],
            help="Useful for MX (sr-barriga). In BR the snapshot already filters open ones.",
        )

    # ---------- Derived flags ----------
    with st.container(border=True):
        st.subheader("🏷️ Derived flags")
        st.caption(
            "Add 1/0 columns marking each row by product type. **Heads-up:** if you "
            "tick only one of the two, the base will be filtered to keep only rows of "
            "that product (e.g. only credit-card customers)."
        )

        cfg["flag_is_cc"] = st.checkbox(
            "is_cc — credit-card flag",
            value=cfg["flag_is_cc"],
            help="Adds an is_cc column (1 if the row is credit-card, 0 otherwise).",
        )
        cfg["flag_is_ll"] = st.checkbox(
            "is_ll — lending flag",
            value=cfg["flag_is_ll"],
            help="Adds an is_ll column (1 if the row is lending, 0 otherwise).",
        )

        cured_help = (
            "Adds a `cured` column (1 if the collection is cured, 0 otherwise). "
            + (
                "BR sources it directly from `collection__cured` on the daily snapshot."
                if cfg["country"] == "BR"
                else "MX derives it from `collection__end.isNotNull` (cured = ended)."
            )
        )
        cfg["flag_cured"] = st.checkbox(
            "cured — collection cured flag",
            value=cfg["flag_cured"],
            help=cured_help,
        )
        if cfg["flag_cured"] and cfg["filter_collection_end_null"]:
            st.warning(
                "⚠️ Cured flag + 'only open collections' filter conflict: cured "
                "customers (collection__end is set) will be excluded. Untick one."
            )

        # Hint about the auto-filter behavior
        if cfg["flag_is_cc"] and not cfg["flag_is_ll"]:
            st.info("ℹ️ Only is_cc is on → base will be filtered to credit-card rows only.")
        elif cfg["flag_is_ll"] and not cfg["flag_is_cc"]:
            st.info("ℹ️ Only is_ll is on → base will be filtered to lending rows only.")

with col_right:
    # ---------- Segmentation ----------
    with st.container(border=True):
        st.subheader("🎯 Segmentation")
        st.caption("Tag each customer with a category for later analysis.")

        cfg["segment_lateness"] = st.checkbox(
            "lateness — short / long",
            value=cfg["segment_lateness"],
            help="Tags a customer 'long' if their days_late is >= cutoff, otherwise 'short'.",
        )
        if cfg["segment_lateness"]:
            cfg["lateness_cutoff"] = st.number_input(
                "Cutoff (days)",
                min_value=1,
                value=cfg["lateness_cutoff"],
            )

        seg_disabled = not (cfg["flag_is_cc"] and cfg["flag_is_ll"])
        cfg["segment_product"] = st.checkbox(
            "segment — cc_only / ll_only / multi_debt",
            value=cfg["segment_product"] and not seg_disabled,
            disabled=seg_disabled,
            help=(
                "Classifies each customer based on whether they have credit-card debt, "
                "lending debt, or both. Requires both is_cc AND is_ll flags above."
            ),
        )
        if seg_disabled and cfg["segment_product"]:
            cfg["segment_product"] = False
        if seg_disabled:
            st.caption("⚠️ Enable both is_cc and is_ll above to unlock this option.")

        # ----- Income segments (BR only) -----
        # Pattern: a single check enables joining `income_segments` from
        # br-segments-v5, and three sub-checkboxes pick which values to keep.
        # Picking a strict subset filters the base; picking all 3 just
        # attaches the column.
        is_br = cfg["country"] == "BR"
        income_label = "Income segments — Mass Market / Super Core / High Income"
        cfg["segment_income"] = st.checkbox(
            income_label,
            value=cfg["segment_income"] and is_br,
            disabled=not is_br,
            help=(
                "Adds the `income_segments` column (joined from "
                "`dataset/br-segments-v5`) and keeps only the values you pick "
                "below. Tick one to filter (e.g. Mass Market only) or tick "
                "all three to just attach the column without filtering."
            ),
        )
        if not is_br and cfg["segment_income"]:
            cfg["segment_income"] = False
        if not is_br:
            st.caption("⚠️ Income segments is BR-only.")

        if cfg["segment_income"]:
            current_inc = [
                v for v in cfg.get("income_segment_values", ALL_INCOME_SEGMENTS)
                if v in ALL_INCOME_SEGMENTS
            ] or list(ALL_INCOME_SEGMENTS)

            # 3-in-a-row checkboxes — visually close to "cc_only ll_only multi_debt"
            picked: list[str] = []
            inc_cols = st.columns(len(ALL_INCOME_SEGMENTS))
            for col, value in zip(inc_cols, ALL_INCOME_SEGMENTS):
                with col:
                    ticked = st.checkbox(
                        INCOME_SEGMENT_LABELS[value],
                        value=value in current_inc,
                    )
                    if ticked:
                        picked.append(value)
            cfg["income_segment_values"] = picked

            if not picked:
                st.warning(
                    "Pick at least one income segment, otherwise the base will be empty."
                )
            elif len(picked) == len(ALL_INCOME_SEGMENTS):
                st.caption(
                    "ℹ️ All three picked → no filter applied, the `income_segments` "
                    "column is just attached to the output."
                )

        # ----- Split mode -----
        # What to do with the segment / lateness tags after they exist.
        # Available depending on which segmentation checks are on:
        #   - "all"     : always (default)
        #   - "single"  : requires segment_product (filter by one segment)
        #   - "multi"   : requires segment_product OR segment_lateness
        has_seg = cfg["segment_product"]
        has_late = cfg["segment_lateness"]
        any_seg = has_seg or has_late

        # Determine the current top-level mode from the config flags
        if cfg.get("multi_save_mode", "none") != "none":
            current_mode = "multi"
        elif cfg.get("filter_segment_only"):
            current_mode = "single"
        else:
            current_mode = "all"

        # Reset to "all" if the prerequisites are no longer met
        if not any_seg and current_mode != "all":
            current_mode = "all"
            cfg["filter_segment_only"] = False
            cfg["multi_save_mode"] = "none"
        elif not has_seg and current_mode == "single":
            current_mode = "all"
            cfg["filter_segment_only"] = False

        mode_labels = {
            "all": "Keep all segments in one save",
            "single": "Filter to one segment",
            "multi": "Multi-save — one base per combination",
        }
        mode_disabled = {
            "all": False,
            "single": not has_seg,
            "multi": not any_seg,
        }

        new_mode = st.radio(
            "Split mode",
            options=list(mode_labels.keys()),
            index=list(mode_labels.keys()).index(current_mode),
            format_func=lambda k: mode_labels[k]
            + ("  (needs segment classification)" if mode_disabled[k] and k == "single"
               else "  (needs segment or lateness)" if mode_disabled[k] and k == "multi"
               else ""),
            disabled=not any_seg,
            help=(
                "How to use the segment / lateness columns:\n"
                "- *Keep all*: one save with the columns included.\n"
                "- *Filter to one*: drop everyone except a chosen segment.\n"
                "- *Multi-save*: produce one base per combination of values "
                "(by segment, by lateness, or both)."
            ),
        )
        if mode_disabled.get(new_mode, False):
            new_mode = "all"
        cfg["filter_segment_only"] = (new_mode == "single")
        if new_mode != "multi":
            cfg["multi_save_mode"] = "none"

        # ----- Single-segment filter -----
        if new_mode == "single":
            cfg["segment_only_value"] = st.radio(
                "Segment to keep",
                ALL_SEGMENTS,
                index=ALL_SEGMENTS.index(cfg.get("segment_only_value", "cc_only")),
                horizontal=True,
            )

        # ----- Multi-save sub-mode + subset pickers -----
        if new_mode == "multi":
            # Which sub-modes are available depends on which segmentation
            # checkboxes are on
            available_submodes: list[str] = []
            if has_seg:
                available_submodes.append("segment")
            if has_late:
                available_submodes.append("lateness")
            if has_seg and has_late:
                available_submodes.append("segment_lateness")

            sub_labels = {
                "segment": "By segment (cc_only / ll_only / multi_debt)",
                "lateness": "By lateness (short / long)",
                "segment_lateness": "By segment × lateness (cross product)",
            }

            current_sub = cfg.get("multi_save_mode", "none")
            if current_sub not in available_submodes:
                current_sub = available_submodes[0]

            new_sub = st.radio(
                "Multi-save by",
                options=available_submodes,
                index=available_submodes.index(current_sub),
                format_func=lambda k: sub_labels[k],
                help=(
                    "Pick the dimension(s) to split on. Each unique combination "
                    "of values becomes its own saved table."
                ),
            )
            cfg["multi_save_mode"] = new_sub

            # Subset pickers
            if new_sub in ("segment", "segment_lateness"):
                current_segs = [
                    s for s in cfg.get("multi_save_segments", ALL_SEGMENTS)
                    if s in ALL_SEGMENTS
                ] or list(ALL_SEGMENTS)
                cfg["multi_save_segments"] = st.multiselect(
                    "Which segments?",
                    options=ALL_SEGMENTS,
                    default=current_segs,
                    help=(
                        "Pick 1 or more segments. Tip: untick to skip a segment "
                        "(e.g. leave 'multi_debt' off if you only want CC-only and LL-only)."
                    ),
                )
                if not cfg["multi_save_segments"]:
                    st.warning("Pick at least one segment.")

            if new_sub in ("lateness", "segment_lateness"):
                current_lates = [
                    l for l in cfg.get("multi_save_lateness", ALL_LATENESS)
                    if l in ALL_LATENESS
                ] or list(ALL_LATENESS)
                cfg["multi_save_lateness"] = st.multiselect(
                    "Which lateness values?",
                    options=ALL_LATENESS,
                    default=current_lates,
                    help=(
                        "Pick 'short', 'long', or both. With just one selected this "
                        "behaves more like a filter — pick both for the typical case."
                    ),
                )
                if not cfg["multi_save_lateness"]:
                    st.warning("Pick at least one lateness value.")

            # Live preview of the resulting table names
            preview = multi_save_names(cfg)
            if preview and preview != [cfg.get("output_name", "")]:
                st.caption(f"Will produce these {len(preview)} tables:")
                for name in preview:
                    st.code(name, language="text")

        if not any_seg:
            st.caption(
                "⚠️ Enable 'lateness' or 'segment' above to unlock split / multi-save options."
            )

    # ---------- Compliance ----------
    with st.container(border=True):
        st.subheader("🔒 Compliance")
        st.caption("Drop customers who shouldn't be included for privacy or compliance reasons.")

        country_tags = forbidden_tags_for(cfg["country"])

        if cfg["country"] == "MX":
            compliance_help = (
                "Excludes customers whose `customer__tags` contain any of the forbidden "
                "substrings (case-insensitive match via `instr`). Source: the SR Barriga "
                "daily snapshot. Used by the team's research / PDP outbound bases."
            )
        else:
            compliance_help = (
                "Excludes customers tagged as journalists, employees, fraud, deceased, etc. "
                "Exact-match against `customer__tags` from the customers dataset. "
                "Usually required for BR research / outbound bases."
            )

        cfg["apply_forbidden_tags_filter"] = st.checkbox(
            f"Apply forbidden_tags filter ({len(country_tags)} tags · {cfg['country']})",
            value=cfg["apply_forbidden_tags_filter"],
            help=compliance_help,
        )

        if cfg["country"] == "MX":
            st.caption(
                "ℹ️ MX uses substring matching (case-insensitive). Tag list and rule "
                "differ from BR — review the generated Scala if unsure."
            )

        with st.expander(f"See the {len(country_tags)} {cfg['country']} tags"):
            st.write(", ".join(f"`{t}`" for t in country_tags))

    # ---------- Enrichment (BR-only join-based extras) ----------
    with st.container(border=True):
        st.subheader("🧩 Enrichment")
        st.caption(
            "Attach extra info to your base by joining BR-only lookup datasets."
        )

        is_br = cfg["country"] == "BR"

        cfg["flag_roxinho"] = st.checkbox(
            "Roxinho only flag",
            value=cfg["flag_roxinho"],
            disabled=not is_br,
            help=(
                "Filters the base to customers in the current Roxinho list. "
                "Joins `nu-br/dataset/current-roxinho-customers`, builds a "
                "`roxinho` column (1 = matched), and keeps only those rows "
                "via `.where($\"roxinho\" === 1)`."
            ),
        )

        if not is_br:
            st.caption(
                "⚠️ This enrichment uses a BR-only dataset. Switch to BR to enable, "
                "or leave it off for MX."
            )
            # Defensive: force off in MX to keep config consistent
            if cfg["flag_roxinho"]:
                cfg["flag_roxinho"] = False

    # ---------- Output ----------
    with st.container(border=True):
        st.subheader("💾 Output")
        st.caption("Pick which columns to keep and how many rows to sample.")

        avail_cols = available_select_columns(cfg)

        # Keep only currently-valid selections — drop any that became
        # unavailable when the user toggled checks above
        current = [c for c in cfg["select_columns"] if c in avail_cols]
        cfg["select_columns"] = st.multiselect(
            "Columns to keep",
            options=avail_cols,
            default=current,
            help=(
                "Only columns that exist on the base at this point are listed — "
                "this prevents typos and 'column not found' errors when you run the notebook."
            ),
        )
        if not cfg["select_columns"]:
            st.warning("Pick at least one column.")

        cfg["limit_enabled"] = st.checkbox(
            "Base sample size",
            value=cfg["limit_enabled"],
            help="Caps how many rows end up in the saved base. Useful for tests / research samples.",
        )
        if cfg["limit_enabled"]:
            in_multi = cfg.get("multi_save_mode", "none") != "none"
            combos = multi_save_combos(cfg) if in_multi else []

            if not in_multi or not combos:
                # Single-save: one input controls the only output.
                cfg["limit_value"] = st.number_input(
                    "Number of rows",
                    min_value=1,
                    value=cfg["limit_value"],
                    step=10000,
                )
            else:
                # Multi-save: one input per resulting table. Useful when, e.g.,
                # the "long overdue" base needs 500k rows but the "short overdue"
                # one only needs 300k. Values default to the previous global
                # `limit_value`; edits are stored per-combo in `limit_values`.
                st.caption(
                    "One row count per saved base — handy when each segment "
                    "needs a different sample size."
                )

                names = multi_save_names(cfg)
                existing = dict(cfg.get("limit_values") or {})
                new_overrides: dict[str, int] = {}

                for (seg, late), table in zip(combos, names):
                    key = combo_key(seg, late)
                    seed = int(existing.get(key, cfg["limit_value"]))
                    val = st.number_input(
                        f"Rows for `{table}`",
                        min_value=1,
                        value=seed,
                        step=10000,
                    )
                    new_overrides[key] = int(val)

                cfg["limit_values"] = new_overrides


# ---------------------------------------------------------------------------
# Advanced settings — power-user options, collapsed by default
# ---------------------------------------------------------------------------

with st.expander("⚙️ Advanced settings", expanded=cfg["groupby_customer_id"]):
    st.caption(
        "Power-user options that most bases don't need. Skip this section unless "
        "you know you need one of these — or a teammate told you to enable it."
    )

    with st.container(border=True):
        st.subheader("📊 Aggregation")
        st.caption("Collapse multiple rows per customer into a single row.")

        cfg["groupby_customer_id"] = st.checkbox(
            "Group by customer__id (one row per customer)",
            value=cfg["groupby_customer_id"],
            help=(
                "Useful when a single customer has multiple collections and you want one row each. "
                "Applies max() on the flags and on product__days_late."
            ),
        )


# ---------------------------------------------------------------------------
# Validation block
# ---------------------------------------------------------------------------

st.divider()
st.subheader("✅ Validation")

errors, warnings = validate_config(cfg)
if errors:
    st.error(f"❌ {len(errors)} error(s) — these block code generation:")
    for e in errors:
        st.markdown(f"- {e}")
if warnings:
    st.warning(f"⚠️ {len(warnings)} warning(s) — these don't block, but please review:")
    for w in warnings:
        st.markdown(f"- {w}")
if not errors and not warnings:
    st.success("All good — ready to generate the Scala.")


# ---------------------------------------------------------------------------
# Preview & download
# ---------------------------------------------------------------------------

st.divider()
st.subheader("📄 Generated Scala")

if errors:
    st.info("Fix the errors above to generate the Scala.")
else:
    scala = render_scala(cfg)
    n_lines = len(scala.splitlines())
    n_bytes = len(scala.encode("utf-8"))

    c1, c2 = st.columns([1, 4])
    with c1:
        st.download_button(
            "💾 Download .scala",
            scala,
            file_name=f"{cfg['output_name']}.scala",
            mime="text/plain",
            type="primary",
            use_container_width=True,
        )
    with c2:
        st.caption(f"{n_lines} lines · {n_bytes:,} bytes · file: `{cfg['output_name']}.scala`")

    st.code(scala, language="scala")

    with st.expander("📋 How to use this file in Databricks"):
        st.markdown(
            """
            1. Click **Download .scala** above
            2. In your Databricks workspace: `File → Import → Drop file or click to browse`
            3. Pick the file you just downloaded
            4. Attach it to a cluster and run cell by cell
            5. **Always review the code before running** on production data — the generator
               builds the skeleton, but more advanced patterns (window functions, multi-source
               joins, renegotiations) still need manual editing
            """
        )

    # -----------------------------------------------------------------------
    # Run on Databricks → CSV (beta)
    # -----------------------------------------------------------------------
    st.divider()
    st.subheader("⚡ Run on Databricks & export CSV (beta)")

    st.warning(
        "This **executes** the generated notebook on a cluster against production "
        "data and writes the full result as CSV. The output contains personal data "
        "(PII) — only run authorized, reviewed bases.",
        icon="⚠️",
    )

    tables = output_table_names(cfg)

    st.caption(
        "Runs **interactively** (execution context) on the cluster — so "
        "interactive-only clusters work. The cluster must be **running**."
    )

    with st.form("run_form"):
        cluster_id = st.text_input(
            "Existing cluster (ID or name)",
            value=st.session_state.get("run_cluster_id", ""),
            placeholder="0123-456789-abcdefgh  or  my-cluster-name",
            help="A running cluster you can execute on. Accepts the cluster ID "
            "or its display name. Use the expander below to list clusters.",
        )
        volume_dir = st.text_input(
            "UC Volume output directory",
            value=st.session_state.get("run_volume_dir", ""),
            placeholder="/Volumes/<catalog>/<schema>/<volume>/base_generator",
            help="A Unity Catalog Volume path the app's identity can write to. "
            "One subfolder per table is created here.",
        )
        submitted = st.form_submit_button(
            "▶️ Run & build CSV", type="primary", use_container_width=True
        )

    st.caption(
        "Will produce: "
        + ", ".join(f"`{t}`" for t in tables)
        + (" — one CSV each." if len(tables) > 1 else " — one CSV.")
    )

    def _user_token():
        """Forwarded user access token for on-behalf-of-user auth (deployed app).

        Read fresh every call — never cache it (tokens rotate per request). Returns
        None locally, where the SDK uses your CLI profile instead.
        """
        try:
            return st.context.headers.get("x-forwarded-access-token")
        except Exception:
            return None

    def _require_runner():
        """Import runner and verify the SDK is present, or stop with a message."""
        try:
            import runner as _r
        except ImportError:
            st.error(
                "`databricks-sdk` is not installed. `pip install -r requirements.txt` "
                "and restart."
            )
            st.stop()
        if not _r.sdk_available():
            st.error(
                "`databricks-sdk` isn't installed in this environment, so the app "
                "can't reach Databricks.\n\n"
                "- **Local run:** `pip install -r requirements.txt`, then restart.\n"
                "- **Deployed app:** redeploy so it reinstalls `requirements.txt`."
            )
            st.stop()
        return _r

    with st.expander("🔎 List clusters I can use"):
        if st.button("Load clusters"):
            runner = _require_runner()
            try:
                with st.spinner("Fetching clusters…"):
                    clusters = runner.list_clusters(user_token=_user_token())
                if clusters:
                    st.dataframe(
                        [
                            {"name": n, "id": cid, "state": state}
                            for n, cid, state in clusters
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption(
                        "Use a cluster in **state = RUNNING** — copy its **id** "
                        "(or name) into the field above."
                    )
                else:
                    st.info("No clusters visible to this identity.")
            except Exception as e:
                st.error(f"Couldn't list clusters: {e}")

    if submitted:
        if not cluster_id.strip():
            st.error("Enter a cluster ID or name.")
        elif not volume_dir.strip():
            st.error("Enter a UC Volume output directory.")
        else:
            st.session_state["run_cluster_id"] = cluster_id.strip()
            st.session_state["run_volume_dir"] = volume_dir.strip()

            runner = _require_runner()
            source = render_scala_with_csv_export(cfg, volume_dir.strip())

            import time as _time

            _wall0 = _time.time()
            with st.status(
                f"Running on {cluster_id.strip()}…", expanded=True
            ) as status:
                status.write(f"[{_time.strftime('%H:%M:%S')}] starting run…")
                result = runner.run_interactive(
                    source,
                    tables,
                    cluster_id=cluster_id.strip(),
                    volume_dir=volume_dir.strip(),
                    progress=status.write,
                    user_token=_user_token(),
                )
                _wall = runner._fmt_secs(_time.time() - _wall0)
                # Stamp the clock the instant control returns. If this time is far
                # ahead of the last "cleanup done" line, the OS paused the process
                # (system sleep / App Nap) during the idle return path.
                status.write(
                    f"[{_time.strftime('%H:%M:%S')}] back in app "
                    f"(⏱️ total wall-clock: {_wall})"
                )
                status.update(
                    label=f"Run finished in {_wall}" if result.ok else "Run failed",
                    state="complete" if result.ok else "error",
                    expanded=not result.ok,
                )

            # Persist the outcome so the download buttons survive the reruns
            # that Streamlit triggers on each download click (otherwise only the
            # first CSV — the one clicked — would ever be downloadable).
            st.session_state["last_run"] = {
                "ok": result.ok,
                "message": result.message,
                "csv_paths": dict(result.csv_files),
                "csv_sizes": dict(result.csv_sizes),
                "volume_dir": volume_dir.strip(),
            }
            st.session_state["csv_bytes"] = {}  # fresh download cache per run

    # --- Result (rendered every rerun from session state) --------------------
    last = st.session_state.get("last_run")
    if last is not None:
        if last["ok"]:
            st.success("CSV(s) ready below — download any/all; they stay here.")
            if not last["csv_paths"]:
                st.warning(
                    "The run succeeded but no CSV was found in the Volume "
                    f"(`{last.get('volume_dir', '')}`). Check the code / Volume path."
                )
            else:
                runner = _require_runner()
                cache = st.session_state.setdefault("csv_bytes", {})
                sizes = last.get("csv_sizes", {})
                to_fetch = {n: p for n, p in last["csv_paths"].items() if p not in cache}
                if to_fetch:
                    import time as _time

                    with st.status(
                        "Downloading CSV(s) from the Volume…", expanded=True
                    ) as dstatus:
                        for name, path in to_fetch.items():
                            sz = runner._fmt_size(sizes.get(name)) if sizes else ""
                            dstatus.write(
                                f"▶ Downloading {name}.csv"
                                + (f" ({sz})…" if sz else "…")
                            )
                            t0 = _time.perf_counter()
                            try:
                                cache[path] = runner.download_csv(
                                    path, user_token=_user_token()
                                )
                            except Exception as e:
                                dstatus.write(f"✗ {name}.csv failed: {e}")
                                continue
                            dt = runner._fmt_secs(_time.perf_counter() - t0)
                            dstatus.write(f"✓ {name}.csv — {dt}")
                        dstatus.update(label="Download finished", state="complete")
                for name, path in last["csv_paths"].items():
                    if path not in cache:
                        continue
                    st.download_button(
                        f"💾 Download {name}.csv",
                        cache[path],
                        file_name=f"{name}.csv",
                        mime="text/csv",
                        key=f"dl_{name}",
                    )
        else:
            st.error(last["message"])
