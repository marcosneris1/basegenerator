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
    CUSTOMER_TIERS,
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


# ---------------------------------------------------------------------------
# Widget state model
# ---------------------------------------------------------------------------
# Every checklist widget uses a STABLE `key` and treats `st.session_state` as
# the single source of truth (seeded once from `cfg`). This is deliberate:
# key-less widgets are identified by their render position, so a widget that
# appears/disappears (e.g. a date input revealed by a checkbox) shifts the
# identity of every widget below it and makes their state "snap back". Stable
# keys make identity position-independent, which fixes that.
#
# Because a keyed widget owns its value in session_state, config-driven changes
# (country switch, template load, or gating a control off) must clear/adjust
# those keys — see `_clear_bound_keys` and the helpers' `force_off` / `pop`.

def _register(key: str):
    st.session_state.setdefault("_bound_keys", set()).add(key)


def _clear_bound_keys():
    for k in st.session_state.get("_bound_keys", set()):
        st.session_state.pop(k, None)
    st.session_state["_bound_keys"] = set()


def reset_to_country(country: str):
    st.session_state.config = default_config(country)
    _clear_bound_keys()


def apply_template(name: str):
    st.session_state.config = get_template(name)
    _clear_bound_keys()


cfg = st.session_state.config


def _seed(key: str, value):
    _register(key)
    if key not in st.session_state:
        st.session_state[key] = value


def w_checkbox(label, cfg_key, *, disabled=False, force_off=False, on_change=None, help=None):
    """Keyed checkbox bound to `cfg[cfg_key]`."""
    _seed(cfg_key, bool(cfg[cfg_key]))
    if force_off:
        st.session_state[cfg_key] = False
    st.checkbox(label, key=cfg_key, disabled=disabled, on_change=on_change, help=help)
    cfg[cfg_key] = bool(st.session_state[cfg_key])
    return cfg[cfg_key]


def w_number(label, cfg_key, *, min_value=None, step=None, help=None):
    _seed(cfg_key, cfg[cfg_key])
    kwargs = {}
    if min_value is not None:
        kwargs["min_value"] = min_value
    if step is not None:
        kwargs["step"] = step
    st.number_input(label, key=cfg_key, help=help, **kwargs)
    cfg[cfg_key] = st.session_state[cfg_key]
    return cfg[cfg_key]


def w_text(label, cfg_key, *, help=None):
    _seed(cfg_key, cfg[cfg_key])
    st.text_input(label, key=cfg_key, help=help)
    cfg[cfg_key] = st.session_state[cfg_key]
    return cfg[cfg_key]


def w_radio(label, cfg_key, options, *, format_func=str, horizontal=False, disabled=False, help=None):
    _register(cfg_key)
    # Sanitize the stored selection against the current options.
    current = st.session_state.get(cfg_key, cfg[cfg_key])
    st.session_state[cfg_key] = current if current in options else (
        cfg[cfg_key] if cfg[cfg_key] in options else options[0]
    )
    st.radio(label, options, key=cfg_key, format_func=format_func,
             horizontal=horizontal, disabled=disabled, help=help)
    cfg[cfg_key] = st.session_state[cfg_key]
    return cfg[cfg_key]


def w_multiselect(label, cfg_key, options, *, help=None):
    _register(cfg_key)
    stored = st.session_state.get(cfg_key, cfg[cfg_key]) or []
    st.session_state[cfg_key] = [x for x in stored if x in options]
    st.multiselect(label, options=options, key=cfg_key, help=help)
    cfg[cfg_key] = st.session_state[cfg_key]
    return cfg[cfg_key]


def _excl_on_open_change():
    # Turning on "only open" clears "only cured" (they're mutually exclusive).
    if st.session_state.get("filter_collection_end_null"):
        st.session_state["filter_cured_only"] = False


def _excl_on_cured_change():
    if st.session_state.get("filter_cured_only"):
        st.session_state["filter_collection_end_null"] = False


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

    w_text(
        "Base name (snake_case)",
        "output_name",
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

        w_checkbox(
            "Filter by snapshot date",
            "filter_snapshot_date",
            help='Keeps only rows from a specific day. Adds: .where($"date" === "<date>")',
        )
        if cfg["filter_snapshot_date"]:
            w_text("Date (YYYY-MM-DD)", "snapshot_date")

        w_checkbox(
            "Filter by days_late range",
            "filter_days_late_range",
            help="Keep customers whose collection is overdue between a min and max number of days.",
        )
        if cfg["filter_days_late_range"]:
            c1, c2 = st.columns(2)
            with c1:
                w_number("Min days late", "days_late_low", min_value=0)
            with c2:
                w_number("Max days late", "days_late_high", min_value=0)

        w_checkbox(
            "Filter by customer type",
            "filter_customer_type",
            help="Keep only individuals (person) or businesses (company).",
        )
        if cfg["filter_customer_type"]:
            w_radio("Type", "customer_type", ["person", "company"], horizontal=True)

        # "Only open collections" and "only cured collections" are mutually
        # exclusive (cured collections have ended, so they're never open). The
        # `on_change` callbacks untick the sibling on the SAME click.
        w_checkbox(
            "Only open collections",
            "filter_collection_end_null",
            on_change=_excl_on_open_change,
            help="Useful for MX (sr-barriga). In BR the snapshot already filters open ones.",
        )
        w_checkbox(
            "Only cured collections",
            "filter_cured_only",
            on_change=_excl_on_cured_change,
            help=(
                "Keeps only cured collections. Adds: "
                '.where($"collection__cured" === 1) — reads the native '
                "`collection__cured` 0/1 column on the daily snapshot."
            ),
        )
        if cfg["filter_collection_end_null"] or cfg["filter_cured_only"]:
            st.caption(
                "ℹ️ 'Only open' and 'only cured' are mutually exclusive — "
                "ticking one unticks the other."
            )

    # ---------- Nubank customer tier ----------
    with st.container(border=True):
        st.subheader("💜 Nubank customer tier")
        st.caption(
            "Keep only customers in a given Nubank tier. Each option joins the "
            "tier's dataset, builds a 1/0 column, and keeps only matched rows. "
            "Ticking more than one keeps customers in **all** the picked tiers."
        )

        tier_is_br = cfg["country"] == "BR"
        for tier in CUSTOMER_TIERS:
            w_checkbox(
                f"{tier['label']} only",
                tier["flag"],
                disabled=not tier_is_br,
                force_off=not tier_is_br,
                help=(
                    f"Filters the base to customers in the current {tier['label']} "
                    f"list. Joins `{tier['dataset']}`, builds a `{tier['col']}` "
                    f"column (1 = matched), and keeps only those rows via "
                    f'.where($"{tier["col"]}" === 1). BR-only dataset.'
                ),
            )
        if not tier_is_br:
            st.caption(
                "⚠️ Customer tier filters use BR-only datasets. Switch to BR to enable."
            )

    # ---------- Derived flags ----------
    with st.container(border=True):
        st.subheader("🏷️ Derived flags")
        st.caption(
            "Add 1/0 columns marking each row by product type. **Heads-up:** if you "
            "tick only one of the two, the base will be filtered to keep only rows of "
            "that product (e.g. only credit-card customers)."
        )

        w_checkbox(
            "is_cc — credit-card flag",
            "flag_is_cc",
            help="Adds an is_cc column (1 if the row is credit-card, 0 otherwise).",
        )
        w_checkbox(
            "is_ll — lending flag",
            "flag_is_ll",
            help="Adds an is_ll column (1 if the row is lending, 0 otherwise).",
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

        w_checkbox(
            "lateness — short / long",
            "segment_lateness",
            help="Tags a customer 'long' if their days_late is >= cutoff, otherwise 'short'.",
        )
        if cfg["segment_lateness"]:
            w_number("Cutoff (days)", "lateness_cutoff", min_value=1)

        seg_disabled = not (cfg["flag_is_cc"] and cfg["flag_is_ll"])
        w_checkbox(
            "segment — cc_only / ll_only / multi_debt",
            "segment_product",
            disabled=seg_disabled,
            force_off=seg_disabled,
            help=(
                "Classifies each customer based on whether they have credit-card debt, "
                "lending debt, or both. Requires both is_cc AND is_ll flags above."
            ),
        )
        if seg_disabled:
            st.caption("⚠️ Enable both is_cc and is_ll above to unlock this option.")

        # ----- Income segments (BR only) -----
        # Pattern: a single check enables joining `income_segments` from
        # br-segments-v5, and three sub-checkboxes pick which values to keep.
        # Picking a strict subset filters the base; picking all 3 just
        # attaches the column.
        is_br = cfg["country"] == "BR"
        income_label = "Income segments — Mass Market / Super Core / High Income"
        w_checkbox(
            income_label,
            "segment_income",
            disabled=not is_br,
            force_off=not is_br,
            help=(
                "Adds the `income_segments` column (joined from "
                "`dataset/br-segments-v5`) and keeps only the values you pick "
                "below. Tick one to filter (e.g. Mass Market only) or tick "
                "all three to just attach the column without filtering."
            ),
        )
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
                    inc_key = f"income_val_{value}"
                    _seed(inc_key, value in current_inc)
                    st.checkbox(INCOME_SEGMENT_LABELS[value], key=inc_key)
                    if st.session_state[inc_key]:
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

        # Initial mode derived from the config flags (used only to seed the
        # keyed widget the first time / after a reset).
        if cfg.get("multi_save_mode", "none") != "none":
            current_mode = "multi"
        elif cfg.get("filter_segment_only"):
            current_mode = "single"
        else:
            current_mode = "all"

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
        mode_options = list(mode_labels.keys())

        # Keyed radio: session_state is the source of truth. Sanitize the
        # stored selection against the current prerequisites BEFORE rendering
        # (Streamlit forbids mutating a widget's state after it's created).
        _register("ui_split_mode")
        stored_mode = st.session_state.get("ui_split_mode", current_mode)
        if not any_seg or stored_mode not in mode_options:
            stored_mode = "all"
        elif not has_seg and stored_mode == "single":
            stored_mode = "all"
        st.session_state["ui_split_mode"] = stored_mode

        st.radio(
            "Split mode",
            options=mode_options,
            key="ui_split_mode",
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
        new_mode = st.session_state["ui_split_mode"]
        cfg["filter_segment_only"] = (new_mode == "single")
        if new_mode != "multi":
            cfg["multi_save_mode"] = "none"

        # ----- Single-segment filter -----
        if new_mode == "single":
            w_radio("Segment to keep", "segment_only_value", ALL_SEGMENTS, horizontal=True)

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

            new_sub = w_radio(
                "Multi-save by",
                "multi_save_mode",
                available_submodes,
                format_func=lambda k: sub_labels[k],
                help=(
                    "Pick the dimension(s) to split on. Each unique combination "
                    "of values becomes its own saved table."
                ),
            )

            # Subset pickers
            if new_sub in ("segment", "segment_lateness"):
                w_multiselect(
                    "Which segments?",
                    "multi_save_segments",
                    ALL_SEGMENTS,
                    help=(
                        "Pick 1 or more segments. Tip: untick to skip a segment "
                        "(e.g. leave 'multi_debt' off if you only want CC-only and LL-only)."
                    ),
                )
                if not cfg["multi_save_segments"]:
                    st.warning("Pick at least one segment.")

            if new_sub in ("lateness", "segment_lateness"):
                w_multiselect(
                    "Which lateness values?",
                    "multi_save_lateness",
                    ALL_LATENESS,
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

        w_checkbox(
            f"Apply forbidden_tags filter ({len(country_tags)} tags · {cfg['country']})",
            "apply_forbidden_tags_filter",
            help=compliance_help,
        )

        if cfg["country"] == "MX":
            st.caption(
                "ℹ️ MX uses substring matching (case-insensitive). Tag list and rule "
                "differ from BR — review the generated Scala if unsure."
            )

        with st.expander(f"See the {len(country_tags)} {cfg['country']} tags"):
            st.write(", ".join(f"`{t}`" for t in country_tags))

    # ---------- Output ----------
    with st.container(border=True):
        st.subheader("💾 Output")
        st.caption("Pick which columns to keep and how many rows to sample.")

        avail_cols = available_select_columns(cfg)

        # Keyed multiselect: session_state is the source of truth. Options can
        # shrink when the user toggles checks above, so w_multiselect drops any
        # now-invalid selections before rendering (avoids a Streamlit error).
        w_multiselect(
            "Columns to keep",
            "select_columns",
            avail_cols,
            help=(
                "Only columns that exist on the base at this point are listed — "
                "this prevents typos and 'column not found' errors when you run the notebook."
            ),
        )
        if not cfg["select_columns"]:
            st.warning("Pick at least one column.")

        w_checkbox(
            "Base sample size",
            "limit_enabled",
            help="Caps how many rows end up in the saved base. Useful for tests / research samples.",
        )
        if cfg["limit_enabled"]:
            in_multi = cfg.get("multi_save_mode", "none") != "none"
            combos = multi_save_combos(cfg) if in_multi else []

            if not in_multi or not combos:
                # Single-save: one input controls the only output.
                w_number("Number of rows", "limit_value", min_value=1, step=10000)
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
                    ss_key = f"limit_combo_{key}"
                    _seed(ss_key, int(existing.get(key, cfg["limit_value"])))
                    st.number_input(
                        f"Rows for `{table}`",
                        min_value=1,
                        step=10000,
                        key=ss_key,
                    )
                    new_overrides[key] = int(st.session_state[ss_key])

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

        w_checkbox(
            "Group by customer__id (one row per customer)",
            "groupby_customer_id",
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
        "data and writes the full result as CSV.",
        icon="⚠️",
    )

    tables = output_table_names(cfg)

    import os as _os

    def _user_token():
        """Forwarded user access token for on-behalf-of-user auth (deployed app).

        Read fresh every call — never cache it (tokens rotate per request). Returns
        None locally, where the SDK uses your CLI profile instead.
        """
        try:
            return st.context.headers.get("x-forwarded-access-token")
        except Exception:
            return None

    def _user_email():
        """The logged-in user, from headers the Databricks App forwards.

        `X-Forwarded-Email` / `X-Forwarded-Preferred-Username` are sent even
        without on-behalf-of-user auth, so each run can be named after the user.
        Returns None locally.
        """
        try:
            h = st.context.headers
            return h.get("x-forwarded-email") or h.get(
                "x-forwarded-preferred-username"
            )
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

    _DEFAULT_JOB_ID = _os.getenv("BASE_GENERATOR_JOB_ID", "109425859584826")
    _DEFAULT_VOLUME = _os.getenv(
        "BASE_GENERATOR_VOLUME", "/Volumes/usr/basegenerator/base_generator_volume/"
    )
    _DEFAULT_NB_DIR = _os.getenv(
        "BASE_GENERATOR_NOTEBOOK_DIR", "/Shared/base_generator/runs"
    )

    _tables_line = (
        "Will produce: "
        + ", ".join(f"`{t}`" for t in tables)
        + (" — one CSV each." if len(tables) > 1 else " — one CSV.")
    )

    # Job and Volume are fixed infrastructure — the user never sees or edits them
    # (override via BASE_GENERATOR_JOB_ID / BASE_GENERATOR_VOLUME env vars).
    job_id = _DEFAULT_JOB_ID
    volume_base = _DEFAULT_VOLUME

    if True:
        st.caption(
            "Builds the base on Databricks and gives you the CSV(s) to download. "
            "Just click below — it can take a few minutes."
        )
        with st.form("job_form"):
            timeout_min_job = st.number_input(
                "Timeout (minutes)", min_value=5, max_value=240, value=60, step=5,
                help="How long to wait for the run before giving up.",
            )
            job_submitted = st.form_submit_button(
                "▶️ Run & build CSV", type="primary", use_container_width=True
            )
        st.caption(_tables_line)

        if job_submitted:
            if not job_id or not volume_base:
                st.error(
                    "The app isn't configured with a Job/Volume. Set "
                    "BASE_GENERATOR_JOB_ID and BASE_GENERATOR_VOLUME (or defaults)."
                )
            else:
                runner = _require_runner()

                def _render(vol_dir):
                    return render_scala_with_csv_export(cfg, vol_dir)

                def _on_started(ctx):
                    # Persist the run context the instant the Job is triggered, so a
                    # browser reconnect mid-wait doesn't lose the ability to fetch
                    # the CSV afterwards (recovery button below).
                    st.session_state["job_ctx"] = {
                        "job_run_id": ctx["job_run_id"],
                        "vol_dir": ctx["vol_dir"],
                        "run_id": ctx["run_id"],
                        "tables": list(tables),
                        "timeout_min": int(timeout_min_job),
                    }

                import time as _time

                _wall0 = _time.time()
                with st.status(
                    "Building your base on Databricks…", expanded=True
                ) as status:
                    status.write(f"[{_time.strftime('%H:%M:%S')}] starting run…")
                    result = runner.run_via_job(
                        _render,
                        tables,
                        job_id=job_id,
                        volume_base=volume_base,
                        progress=status.write,
                        timeout_min=int(timeout_min_job),
                        # Job + Volume are granted to the app's service principal
                        # via resources, so run as the SP (never the user token).
                        user_token=None,
                        on_started=_on_started,
                        # Name each run after the logged-in user + a random suffix
                        # so users' runs are isolated and never collide.
                        run_id_prefix=_user_email(),
                        notebook_dir=_DEFAULT_NB_DIR,
                    )
                    _wall = runner._fmt_secs(_time.time() - _wall0)
                    status.write(
                        f"[{_time.strftime('%H:%M:%S')}] back in app "
                        f"(⏱️ total wall-clock: {_wall})"
                    )
                    status.update(
                        label=f"Run finished in {_wall}" if result.ok else "Run failed",
                        state="complete" if result.ok else "error",
                        expanded=not result.ok,
                    )

                st.session_state["last_run"] = {
                    "ok": result.ok,
                    "message": result.message,
                    "csv_paths": dict(result.csv_files),
                    "csv_sizes": dict(result.csv_sizes),
                    "volume_dir": volume_base,
                    "use_obo": False,  # CSV lives in the SP-readable Volume
                }
                st.session_state["csv_bytes"] = {}
                # Clear the recovery context once we have a successful result.
                if result.ok and result.csv_files:
                    st.session_state.pop("job_ctx", None)

        # --- Recovery: fetch the CSV of a triggered run without re-running -----
        ctx = st.session_state.get("job_ctx")
        last = st.session_state.get("last_run")
        _need_recovery = ctx and not (
            last and last.get("ok") and last.get("csv_paths")
        )
        if _need_recovery:
            st.info(
                "A Job run was triggered "
                f"(run `{ctx['job_run_id']}`). If the page reconnected during the "
                "wait, the result may not have loaded. Fetch it here without "
                "re-running the Job."
            )
            if st.button("🔄 Check run & fetch CSV(s)"):
                runner = _require_runner()
                with st.status("Checking the Job run…", expanded=True) as rstatus:
                    result = runner.fetch_job_result(
                        job_run_id=ctx["job_run_id"],
                        vol_dir=ctx["vol_dir"],
                        table_names=ctx["tables"],
                        progress=rstatus.write,
                        timeout_min=int(ctx.get("timeout_min", 60)),
                        user_token=None,
                    )
                    rstatus.update(
                        label="Done" if result.ok else "Run failed",
                        state="complete" if result.ok else "error",
                        expanded=not result.ok,
                    )
                st.session_state["last_run"] = {
                    "ok": result.ok,
                    "message": result.message,
                    "csv_paths": dict(result.csv_files),
                    "csv_sizes": dict(result.csv_sizes),
                    "volume_dir": ctx["vol_dir"],
                    "use_obo": False,
                }
                st.session_state["csv_bytes"] = {}
                if result.ok and result.csv_files:
                    st.session_state.pop("job_ctx", None)
                st.rerun()

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
                # Job mode reads via the service principal (Volume resource); the
                # interactive mode reads back as the same user that ran it.
                dl_token = _user_token() if last.get("use_obo", True) else None
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
                                    path, user_token=dl_token
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
