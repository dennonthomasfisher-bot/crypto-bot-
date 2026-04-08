"""
Gematria Event Scanner MVP
A Streamlit dashboard for gematria analysis, frequency tracking, and control group comparison.
"""

import csv
import os
import random
import string
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date

import streamlit as st
import pandas as pd

# --- Gematria Functions ---

def ordinal_gematria(text):
    """Calculate English ordinal gematria (A=1, B=2, ... Z=26)."""
    total = 0
    for char in text.upper():
        if 'A' <= char <= 'Z':
            total += ord(char) - ord('A') + 1
    return total


def reduce_number(n):
    """Reduce a number to a single digit by summing its digits repeatedly."""
    while n >= 10:
        n = sum(int(d) for d in str(n))
    return n


def date_sum(d):
    """Sum all digits of a date (YYYY-MM-DD)."""
    return sum(int(ch) for ch in str(d) if ch.isdigit())


def signal_score(ordinal, reduced, scan_date, log_df):
    """Data-driven signal score — no hardcoded 'special' numbers.

    Scoring layers:
    1. Frequency rarity   (0-30): how rare is this ordinal in the existing log?
    2. Date-ordinal match (0-25): does the ordinal equal the date digit sum?
    3. Date-reduced match (0-30): does the reduced value equal the reduced date sum?
    4. Multi-signal bonus  (0-15): reward when multiple layers align
    5. Weak signal penalty   (-10): penalize when nothing meaningful matches

    Returns (score, label, reasons) where score is 0-100.
    """
    score = 0
    reasons = []
    matches = 0

    # --- Layer 1: Frequency rarity (nerfed — contributes, doesn't dominate) ---
    if not log_df.empty and len(log_df) >= 5:
        total = len(log_df)
        count = (log_df["ordinal"] == ordinal).sum()
        freq = count / total
        pct = f"{freq:.0%}"

        if freq < 0.05:
            score += 30
            matches += 1
            reasons.append(f"Ordinal {ordinal} is very rare in log ({pct} of entries) → +30")
        elif freq < 0.10:
            score += 20
            matches += 1
            reasons.append(f"Ordinal {ordinal} is rare in log ({pct} of entries) → +20")
        elif freq < 0.20:
            score += 10
            reasons.append(f"Ordinal {ordinal} is uncommon in log ({pct} of entries) → +10")
        elif freq < 0.30:
            score += 5
            reasons.append(f"Ordinal {ordinal} is moderate in log ({pct} of entries) → +5")
        else:
            reasons.append(f"Ordinal {ordinal} is common in log ({pct} of entries) → +0")
    else:
        score += 15
        reasons.append("Not enough data yet (need 5+ entries) — neutral score → +15")

    # --- Layer 2: Ordinal matches date sum ---
    ds = date_sum(scan_date)
    if ordinal == ds:
        score += 25
        matches += 1
        reasons.append(f"Ordinal {ordinal} matches date digit sum {ds} → +25")
    else:
        reasons.append(f"Ordinal {ordinal} does not match date digit sum {ds} → +0")

    # --- Layer 3: Reduced matches reduced date sum ---
    ds_reduced = reduce_number(ds)
    if reduced == ds_reduced:
        score += 30
        matches += 1
        reasons.append(f"Reduced {reduced} matches reduced date sum {ds_reduced} → +30")
    else:
        reasons.append(f"Reduced {reduced} does not match reduced date sum {ds_reduced} → +0")

    # --- Layer 4: Multi-signal bonus ---
    if matches >= 2:
        score += 15
        reasons.append(f"Multiple signals aligned ({matches} layers matched) → +15")

    # --- Layer 5: Weak signal penalty ---
    if matches == 0:
        score -= 10
        reasons.append("No meaningful matches detected → -10")

    score = max(0, min(score, 100))

    # --- Signal label ---
    if score >= 70:
        label = "HIGH SIGNAL"
    elif score >= 40:
        label = "MEDIUM SIGNAL"
    else:
        label = "LOW SIGNAL"

    return score, label, reasons


# --- CSV Logging ---

LOG_FILE = "gematria_log.csv"


def save_entry(text, scan_date, ordinal, reduced, score):
    """Append a scan entry to the CSV log."""
    if len(text) > 300 or ordinal > 5000:
        return
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["text", "date", "ordinal", "reduced", "score"])
        writer.writerow([text, scan_date, ordinal, reduced, score])


def load_log():
    """Load the CSV log into a DataFrame."""
    if os.path.exists(LOG_FILE):
        return pd.read_csv(LOG_FILE)
    return pd.DataFrame(columns=["text", "date", "ordinal", "reduced", "score"])


# --- Control Group ---

def generate_random_word(length=6):
    """Generate a random word of given length."""
    return ''.join(random.choices(string.ascii_lowercase, k=length))


def run_control_group(n=100, word_length=6):
    """Generate n random words and compute their gematria values."""
    results = []
    for _ in range(n):
        word = generate_random_word(word_length)
        ov = ordinal_gematria(word)
        rv = reduce_number(ov)
        results.append({"word": word, "ordinal": ov, "reduced": rv})
    return pd.DataFrame(results)


# --- RSS Feed ---

def fetch_bbc_headlines(n=10):
    """Fetch top headlines from BBC News RSS feed."""
    url = "https://feeds.bbci.co.uk/news/rss.xml"
    req = urllib.request.Request(url, headers={"User-Agent": "GematriaScanner/1.0"})
    with urllib.request.urlopen(req, timeout=10) as response:
        tree = ET.parse(response)
    root = tree.getroot()
    headlines = []
    for item in root.iter("item"):
        title = item.find("title")
        if title is not None and title.text:
            headlines.append(title.text.strip())
            if len(headlines) >= n:
                break
    return headlines


# --- Analysis Functions ---

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "has", "have", "had", "will", "would", "could", "should",
    "may", "might", "do", "does", "did", "not", "it", "its", "this",
    "that", "their", "they", "he", "she", "we", "you", "i", "his", "her",
    "over", "after", "before", "up", "out", "into", "about", "than",
    "what", "who", "which", "how", "says", "said", "new", "more", "s",
}
_COUNTRY_KW = {"us", "iran", "israel", "uk", "russia", "china"}
_TOPIC_KW = {"war", "deal", "strike", "ceasefire", "attack", "collapse", "summit"}
_TRACKED_KW = _COUNTRY_KW | _TOPIC_KW


def _headline_words(text):
    """Lowercase, strip punctuation, split a headline into words."""
    words = text.lower().replace("-", " ").split()
    return {w.strip(".,;:!?\"'()[]") for w in words}


def extract_entities(headlines):
    """Count tracked keywords across headlines. Return top 3 as list of (kw, count)."""
    counts = Counter()
    for h in headlines:
        words = _headline_words(h)
        for kw in _TRACKED_KW:
            if kw in words:
                counts[kw] += 1
    return counts.most_common(3)


def detect_clusters(headlines):
    """Group headlines by shared tracked keywords. Return top clusters."""
    groups = Counter()
    for h in headlines:
        words = _headline_words(h)
        found = sorted(w for w in words if w in _TRACKED_KW)
        if found:
            key = "-".join(found[:2])
            groups[key] += 1
    return [{"topic": t, "count": c} for t, c in groups.most_common(5)]


def classify_signal_phase(auto_results, today):
    """Classify the scan phase based on scored results.

    FORMATION : dominant reduced ≥15%, ≥2 HIGH signals, no date matches
    TRIGGER   : ≥1 date match AND ≥1 HIGH signal
    IMMINENT  : ≥2 date matches AND dominant reduced still present
    BASELINE  : none of the above
    """
    if not auto_results:
        return "BASELINE"

    ds = date_sum(today)
    ds_r = reduce_number(ds)

    high_count = sum(1 for r in auto_results if r["Score"] >= 70)
    date_matches = sum(
        1 for r in auto_results
        if r["Ordinal"] == ds or r["Reduced"] == ds_r
    )

    red_counter = Counter(r["Reduced"] for r in auto_results)
    top_red_pct = red_counter.most_common(1)[0][1] / len(auto_results)

    if date_matches >= 2 and top_red_pct >= 0.15:
        return "IMMINENT"
    if date_matches >= 1 and high_count >= 1:
        return "TRIGGER"
    if top_red_pct >= 0.15 and high_count >= 2 and date_matches == 0:
        return "FORMATION"
    return "BASELINE"


# --- Streamlit App ---

st.set_page_config(page_title="Gematria Event Scanner", layout="wide")
st.title("Gematria Event Scanner")

# --- Signal Intelligence Panel (reads directly from CSV every page load) ---
_panel_df = load_log()
if not _panel_df.empty:
    _today_str = str(date.today())
    _today_df = _panel_df[_panel_df["date"] == _today_str]
    _src = _today_df if not _today_df.empty else _panel_df

    # Compute phase from source data
    _ds = date_sum(date.today())
    _ds_r = reduce_number(_ds)
    _high_count = int((_src["score"] >= 70).sum())
    _date_matches = int(
        ((_src["ordinal"] == _ds) | (_src["reduced"] == _ds_r)).sum()
    )
    _red_counts = _src["reduced"].value_counts()
    _dom_red = int(_red_counts.index[0])
    _dom_red_pct = _red_counts.iloc[0] / len(_src)

    if _date_matches >= 2 and _dom_red_pct >= 0.15:
        _phase = "IMMINENT"
        _alert = "Event imminent — multiple date matches and structural alignment"
    elif _date_matches >= 1 and _high_count >= 1:
        _phase = "TRIGGER"
        _alert = "Trigger phase — date match detected, monitor closely"
    elif _dom_red_pct >= 0.15 and _high_count >= 2 and _date_matches == 0:
        _phase = "FORMATION"
        _alert = "Formation phase — alignment building, no trigger yet"
    else:
        _phase = "BASELINE"
        _alert = "Baseline — no significant phase pattern detected"

    # Entities and clusters from headline text
    _headlines = _src["text"].tolist()
    _entities = extract_entities(_headlines)
    _clusters = detect_clusters(_headlines)

    # Render phase alert
    if _phase == "IMMINENT":
        st.error(f"SIGNAL PHASE: {_phase} — {_alert}")
    elif _phase == "TRIGGER":
        st.warning(f"SIGNAL PHASE: {_phase} — {_alert}")
    elif _phase == "FORMATION":
        st.info(f"SIGNAL PHASE: {_phase} — {_alert}")
    else:
        st.success(f"SIGNAL PHASE: {_phase} — {_alert}")

    _p1, _p2, _p3 = st.columns(3)
    _p1.metric("Dominant Reduced", _dom_red)
    _p2.metric("Top Entities", ", ".join(e for e, _ in _entities) if _entities else "—")
    _p3.metric("Clusters Detected", len(_clusters))

    if _entities or _clusters:
        _ec1, _ec2 = st.columns(2)
        with _ec1:
            st.write("**Top Entities**")
            for _kw, _cnt in _entities:
                st.write(f"- {_kw}: {_cnt}")
        with _ec2:
            st.write("**Clusters**")
            if _clusters:
                for _cl in _clusters:
                    st.write(f"- {_cl['topic']}: {_cl['count']}")
            else:
                st.write("- None detected")

    st.divider()

tab_scan, tab_freq, tab_control = st.tabs(["Scan & Score", "Frequency", "Control Group"])

# --- Tab 1: Scan & Score ---
with tab_scan:
    st.header("Scan & Score")

    col1, col2 = st.columns([3, 1])
    with col1:
        text_input = st.text_input("Enter text (headline, name, phrase):", key="scan_text")
    with col2:
        date_input = st.date_input("Date:", value=date.today(), key="scan_date")

    if st.button("Scan", key="scan_btn"):
        if text_input.strip():
            ov = ordinal_gematria(text_input)
            rv = reduce_number(ov)
            existing_log = load_log()
            sc, label, reasons = signal_score(ov, rv, date_input, existing_log)
            save_entry(text_input, date_input, ov, rv, sc)

            ds = date_sum(date_input)
            ds_r = reduce_number(ds)

            st.subheader("Results")
            c1, c2, c3 = st.columns(3)
            c1.metric("Ordinal Value", ov)
            c2.metric("Reduced Value", rv)
            c3.metric("Signal Score", f"{sc}/100")

            if sc >= 70:
                st.error(f"**{label}**")
            elif sc >= 40:
                st.warning(f"**{label}**")
            else:
                st.info(f"**{label}**")

            st.caption(f"Date sum: {ds} (reduced: {ds_r})")

            st.write("**Why this score?**")
            for reason in reasons:
                st.write(f"- {reason}")
        else:
            st.warning("Please enter some text to scan.")

    st.divider()
    st.subheader("Auto-Scan Latest Headlines")

    if st.button("Auto-Scan BBC News", key="auto_scan_btn"):
        try:
            headlines = fetch_bbc_headlines(10)
            if not headlines:
                st.warning("No headlines fetched.")
            else:
                today = date.today()
                existing_log = load_log()
                auto_results = []

                for headline in headlines:
                    ov = ordinal_gematria(headline)
                    rv = reduce_number(ov)
                    sc, lbl, reasons = signal_score(ov, rv, today, existing_log)
                    save_entry(headline, today, ov, rv, sc)
                    auto_results.append({
                        "Headline": headline,
                        "Ordinal": ov,
                        "Reduced": rv,
                        "Score": sc,
                        "Signal": lbl,
                        "_reasons": reasons,
                    })
                    # Reload log so subsequent scores reflect new entries
                    existing_log = load_log()

                # Show alerts for HIGH signals above the table
                for r in auto_results:
                    if r["Score"] >= 70:
                        reason_lines = "\n".join(f"- {x}" for x in r["_reasons"])
                        st.error(
                            f"**HIGH SIGNAL DETECTED**\n\n"
                            f"**Headline:** {r['Headline']}\n\n"
                            f"**Score:** {r['Score']}/100\n\n"
                            f"**Why this score?**\n{reason_lines}"
                        )

                auto_df = pd.DataFrame([
                    {k: v for k, v in r.items() if k != "_reasons"}
                    for r in auto_results
                ])
                st.dataframe(auto_df, use_container_width=True)
                st.success(f"Scanned and logged {len(auto_results)} headlines.")
        except Exception as e:
            st.error(f"Failed to fetch headlines: {e}")

    st.divider()
    st.subheader("Scan History")
    log_df = load_log()
    if not log_df.empty:
        st.dataframe(log_df.sort_index(ascending=False), use_container_width=True)
    else:
        st.caption("No entries yet. Run a scan above.")

    # --- Pattern Detection ---
    st.divider()
    st.subheader("Pattern Detection")
    pat_df = load_log()
    if not pat_df.empty and len(pat_df) >= 2:
        # 1. Repeating ordinals
        ord_counts = pat_df["ordinal"].value_counts()
        repeats = ord_counts[ord_counts >= 2]

        st.write("**Repeating Ordinals** (appearing 2+ times)")
        if not repeats.empty:
            for val, cnt in repeats.items():
                st.write(f"- Ordinal **{val}** appeared **{cnt}** times")
        else:
            st.write("- No repeating ordinals yet")

        # 2. Dominant reduced value
        red_counts = pat_df["reduced"].value_counts()
        top_reduced = red_counts.index[0]
        top_reduced_count = red_counts.iloc[0]
        top_reduced_pct = top_reduced_count / len(pat_df)

        st.write("**Dominant Reduced Value**")
        r1, r2 = st.columns(2)
        r1.metric("Most Common Reduced", int(top_reduced))
        r2.metric("Frequency", f"{top_reduced_pct:.0%} ({top_reduced_count}/{len(pat_df)})")

        # 3. Score distribution
        high_count = (pat_df["score"] >= 70).sum()
        med_count = ((pat_df["score"] >= 40) & (pat_df["score"] < 70)).sum()
        low_count = (pat_df["score"] < 40).sum()

        st.write("**Score Distribution**")
        d1, d2, d3 = st.columns(3)
        d1.metric("HIGH", high_count)
        d2.metric("MEDIUM", med_count)
        d3.metric("LOW", low_count)
    else:
        st.caption("Need at least 2 entries to detect patterns.")

    # --- Gematria Signal ---
    st.divider()
    st.subheader("Gematria Signal")
    sig_df = load_log()
    if not sig_df.empty and len(sig_df) >= 3:
        # Gather data points
        high_count = (sig_df["score"] >= 70).sum()
        med_count = ((sig_df["score"] >= 40) & (sig_df["score"] < 70)).sum()
        low_count = (sig_df["score"] < 40).sum()

        ord_counts = sig_df["ordinal"].value_counts()
        repeat_count = (ord_counts >= 2).sum()

        red_counts = sig_df["reduced"].value_counts()
        top_reduced = int(red_counts.index[0])
        top_reduced_pct = red_counts.iloc[0] / len(sig_df)

        # Determine field state
        if high_count >= 3:
            field = "HIGH COHERENCE"
        elif high_count >= 1:
            field = "MODERATE ACTIVITY"
        elif med_count > low_count:
            field = "LOW-MODERATE ACTIVITY"
        else:
            field = "LOW ACTIVITY"

        # Build observations from real data
        observations = []

        if high_count >= 2:
            observations.append(f"Multiple high-value alignments detected ({high_count} HIGH signals)")
        elif high_count == 1:
            observations.append("Isolated high signal detected")
        else:
            observations.append("No strong numerical alignment across system")

        if repeat_count >= 3:
            observations.append(f"Repeating ordinal structures forming ({repeat_count} values repeat)")
        elif repeat_count >= 1:
            observations.append(f"Repetition present but non-dominant ({repeat_count} value{'s' if repeat_count > 1 else ''} repeat{'s' if repeat_count == 1 else ''})")
        else:
            observations.append("No ordinal repetition detected")

        if top_reduced_pct > 0.30:
            observations.append(f"Reduced value clustering emerging (reduced {top_reduced} at {top_reduced_pct:.0%})")
        elif top_reduced_pct > 0.20:
            observations.append(f"Mild reduced value concentration (reduced {top_reduced} at {top_reduced_pct:.0%})")
        else:
            observations.append("Reduced values evenly distributed")

        # Interpretation
        if field == "HIGH COHERENCE":
            interp = "Pattern convergence — multiple signals aligning"
        elif field == "MODERATE ACTIVITY":
            interp = "Emerging signal — monitor for escalation"
        elif field == "LOW-MODERATE ACTIVITY":
            interp = "Noise with occasional alignment"
        else:
            interp = "No meaningful pattern structure detected"

        # Render
        obs_lines = "\n".join(f"  - {o}" for o in observations)
        st.code(
            f"GEMATRIA SIGNAL\n\n"
            f"Field: {field}\n\n"
            f"{obs_lines}\n\n"
            f"-> Interpretation: {interp}",
            language=None,
        )
    else:
        st.caption("Need at least 3 entries to generate a signal reading.")

# --- Tab 2: Frequency ---
with tab_freq:
    st.header("Frequency Tracking")

    log_df = load_log()
    if not log_df.empty:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Ordinal Value Frequency")
            ord_counts = log_df["ordinal"].value_counts().sort_values(ascending=False).head(20)
            st.bar_chart(ord_counts)
            st.caption("Top 20 most common ordinal values")

        with col2:
            st.subheader("Reduced Value Frequency")
            red_counts = log_df["reduced"].value_counts().sort_index()
            st.bar_chart(red_counts)
            st.caption("Distribution of reduced values (1-9)")

        st.divider()
        st.subheader("Most Common Numbers")
        top = log_df["ordinal"].value_counts().head(10).reset_index()
        top.columns = ["Ordinal Value", "Count"]
        st.table(top)
    else:
        st.info("No data yet. Scan some entries first.")

# --- Tab 3: Control Group ---
with tab_control:
    st.header("Control Group Analysis")
    st.caption("Generate random words and compare their gematria distribution to your scans.")

    n_words = st.slider("Number of random words:", 50, 500, 100, step=50, key="ctrl_n")
    word_len = st.slider("Word length:", 3, 12, 6, key="ctrl_len")

    if st.button("Generate Control Group", key="ctrl_btn"):
        ctrl_df = run_control_group(n=n_words, word_length=word_len)

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Control: Ordinal Distribution")
            st.bar_chart(ctrl_df["ordinal"].value_counts().sort_index())

        with col2:
            st.subheader("Control: Reduced Distribution")
            st.bar_chart(ctrl_df["reduced"].value_counts().sort_index())

        st.divider()

        # Compare with real data
        log_df = load_log()
        if not log_df.empty:
            st.subheader("Comparison: Your Scans vs Control")
            comp_col1, comp_col2 = st.columns(2)
            with comp_col1:
                st.metric("Your avg ordinal", f"{log_df['ordinal'].mean():.1f}")
                st.metric("Control avg ordinal", f"{ctrl_df['ordinal'].mean():.1f}")
            with comp_col2:
                st.metric("Your avg reduced", f"{log_df['reduced'].mean():.1f}")
                st.metric("Control avg reduced", f"{ctrl_df['reduced'].mean():.1f}")
        else:
            st.info("Scan some entries to see a comparison with the control group.")

        st.divider()
        st.subheader("Control Group Data (sample)")
        st.dataframe(ctrl_df.head(20), use_container_width=True)
