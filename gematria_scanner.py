"""
Gematria Daily Intelligence Dashboard
Multi-source news scanning, signal phase classification, pattern detection,
cross-source matching, and trend analysis.
"""

import csv
import os
import random
import string
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, timedelta

import streamlit as st
import pandas as pd

# --- Config ---

LOG_FILE = "gematria_log.csv"

RSS_FEEDS = {
    "BBC":        "http://feeds.bbci.co.uk/news/rss.xml",
    "Reuters":    "https://feeds.reuters.com/reuters/topNews",
    "Sky News":   "https://feeds.skynews.com/feeds/rss/home.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "RT":         "https://www.rt.com/rss/",
    "Daily Mail": "https://www.dailymail.co.uk/articles.rss",
}

_COUNTRY_KW = {"us", "iran", "israel", "uk", "russia", "china", "ukraine", "gaza", "india", "syria"}
_TOPIC_KW = {"war", "deal", "strike", "ceasefire", "attack", "collapse", "summit",
             "bomb", "missile", "sanctions", "trade", "crisis", "election", "killed"}
_TRACKED_KW = _COUNTRY_KW | _TOPIC_KW

# --- Gematria Functions ---

def ordinal_gematria(text):
    total = 0
    for char in text.upper():
        if 'A' <= char <= 'Z':
            total += ord(char) - ord('A') + 1
    return total


def reduce_number(n):
    while n >= 10:
        n = sum(int(d) for d in str(n))
    return n


def date_sum(d):
    return sum(int(ch) for ch in str(d) if ch.isdigit())


def signal_score(ordinal, reduced, scan_date, log_df):
    score = 0
    reasons = []
    matches = 0

    if not log_df.empty and len(log_df) >= 5:
        total = len(log_df)
        count = (log_df["ordinal"] == ordinal).sum()
        freq = count / total
        pct = f"{freq:.0%}"
        if freq < 0.05:
            score += 30; matches += 1
            reasons.append(f"Ordinal {ordinal} very rare ({pct}) +30")
        elif freq < 0.10:
            score += 20; matches += 1
            reasons.append(f"Ordinal {ordinal} rare ({pct}) +20")
        elif freq < 0.20:
            score += 10
            reasons.append(f"Ordinal {ordinal} uncommon ({pct}) +10")
        elif freq < 0.30:
            score += 5
            reasons.append(f"Ordinal {ordinal} moderate ({pct}) +5")
        else:
            reasons.append(f"Ordinal {ordinal} common ({pct}) +0")
    else:
        score += 15
        reasons.append("Insufficient data — neutral +15")

    ds = date_sum(scan_date)
    if ordinal == ds:
        score += 25; matches += 1
        reasons.append(f"Ordinal matches date sum {ds} +25")
    else:
        reasons.append(f"Ordinal != date sum {ds} +0")

    ds_r = reduce_number(ds)
    if reduced == ds_r:
        score += 30; matches += 1
        reasons.append(f"Reduced {reduced} matches date reduced {ds_r} +30")
    else:
        reasons.append(f"Reduced {reduced} != date reduced {ds_r} +0")

    if matches >= 2:
        score += 15
        reasons.append(f"Multi-signal bonus ({matches} matched) +15")
    if matches == 0:
        score -= 10
        reasons.append("No matches -10")

    score = max(0, min(score, 100))
    if score >= 70:
        label = "HIGH SIGNAL"
    elif score >= 40:
        label = "MEDIUM SIGNAL"
    else:
        label = "LOW SIGNAL"
    return score, label, reasons


# --- CSV ---

def save_entry(text, scan_date, ordinal, reduced, score, source="manual"):
    clean = str(text).strip().replace("\n", " ").replace("\r", "")
    if len(clean) > 300 or ordinal > 5000 or not clean:
        return
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["text", "date", "ordinal", "reduced", "score", "source"])
        writer.writerow([clean, scan_date, ordinal, reduced, score, source])


def load_log():
    if os.path.exists(LOG_FILE):
        df = pd.read_csv(LOG_FILE)
        if "source" not in df.columns:
            df["source"] = "unknown"
        df = df[df["text"].astype(str).str.len() <= 500]
        return df
    return pd.DataFrame(columns=["text", "date", "ordinal", "reduced", "score", "source"])


# --- RSS ---

def fetch_rss_headlines(url, n=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GematriaScanner/2.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            tree = ET.parse(resp)
        headlines = []
        for item in tree.getroot().iter("item"):
            title = item.find("title")
            if title is not None and title.text:
                t = title.text.strip()
                if len(t) > 10 and len(t) <= 300:
                    headlines.append(t)
                    if len(headlines) >= n:
                        break
        return headlines
    except Exception:
        return []


def fetch_all_feeds():
    all_headlines = {}
    for name, url in RSS_FEEDS.items():
        hlines = fetch_rss_headlines(url, n=15)
        if hlines:
            all_headlines[name] = hlines
    return all_headlines


def auto_scan_today():
    """Fetch all feeds and scan if today's data is not already in CSV."""
    today_str = str(date.today())
    log = load_log()
    today_entries = log[log["date"].astype(str) == today_str]
    if len(today_entries) >= 50:
        return log, False  # already scanned today

    all_headlines = fetch_all_feeds()
    if not all_headlines:
        return log, False

    existing_log = load_log()
    for source, headlines in all_headlines.items():
        for headline in headlines:
            ov = ordinal_gematria(headline)
            rv = reduce_number(ov)
            sc, _, _ = signal_score(ov, rv, date.today(), existing_log)
            save_entry(headline, date.today(), ov, rv, sc, source)
            existing_log = load_log()

    return load_log(), True


# --- Analysis Functions ---

def _headline_words(text):
    words = str(text).lower().replace("-", " ").split()
    return {w.strip(".,;:!?\"'()[]") for w in words}


def extract_entities(headlines, top_n=5):
    counts = Counter()
    for h in headlines:
        words = _headline_words(h)
        for kw in _TRACKED_KW:
            if kw in words:
                counts[kw] += 1
    return counts.most_common(top_n)


def detect_clusters(headlines):
    groups = Counter()
    for h in headlines:
        words = _headline_words(h)
        found = sorted(w for w in words if w in _TRACKED_KW)
        if found:
            groups["-".join(found[:2])] += 1
    return [{"topic": t, "count": c} for t, c in groups.most_common(5)]


def compute_phase(df):
    if df.empty:
        return "BASELINE", "No data available"
    ds = date_sum(date.today())
    ds_r = reduce_number(ds)
    high_count = int((df["score"] >= 70).sum())
    date_matches = int(((df["ordinal"] == ds) | (df["reduced"] == ds_r)).sum())
    red_counts = df["reduced"].value_counts()
    dom_red_pct = red_counts.iloc[0] / len(df) if not red_counts.empty else 0

    if date_matches >= 2 and dom_red_pct >= 0.15:
        return "IMMINENT", "Multiple date matches and structural alignment detected"
    if date_matches >= 1 and high_count >= 1:
        return "TRIGGER", "Date match detected with HIGH signal — monitor closely"
    if dom_red_pct >= 0.15 and high_count >= 2 and date_matches == 0:
        return "FORMATION", "Alignment building — no trigger yet"
    return "BASELINE", "No significant phase pattern detected"


def find_cross_source_matches(df):
    """Find headlines from different sources sharing ordinal or reduced on same day."""
    matches = []
    if df.empty or "source" not in df.columns:
        return matches
    for d in df["date"].unique():
        day_df = df[df["date"] == d]
        if day_df["source"].nunique() < 2:
            continue
        for val_col in ["ordinal", "reduced"]:
            val_counts = day_df.groupby(val_col)["source"].nunique()
            shared_vals = val_counts[val_counts >= 2].index.tolist()
            for v in shared_vals:
                rows = day_df[day_df[val_col] == v]
                sources = rows["source"].unique().tolist()
                texts = rows["text"].head(3).tolist()
                matches.append({
                    "date": d,
                    "type": val_col,
                    "value": int(v),
                    "sources": sources,
                    "headlines": texts,
                })
    return matches[:10]


# --- Control Group ---

def run_control_group(n=100, word_length=6):
    results = []
    for _ in range(n):
        word = ''.join(random.choices(string.ascii_lowercase, k=word_length))
        ov = ordinal_gematria(word)
        rv = reduce_number(ov)
        results.append({"word": word, "ordinal": ov, "reduced": rv})
    return pd.DataFrame(results)


# ===================== STREAMLIT APP =====================

st.set_page_config(page_title="Gematria Intelligence Dashboard", layout="wide")
st.title("Gematria Intelligence Dashboard")

# --- Auto-scan on load ---
with st.spinner("Scanning headlines..."):
    log_df, just_scanned = auto_scan_today()
today_str = str(date.today())
today_df = log_df[log_df["date"].astype(str) == today_str] if not log_df.empty else log_df

# --- Signal Intelligence Panel (always visible) ---
if not log_df.empty:
    src = today_df if not today_df.empty else log_df
    phase, alert_msg = compute_phase(src)
    headlines = src["text"].dropna().tolist()
    entities = extract_entities(headlines, top_n=5)
    clusters = detect_clusters(headlines)
    red_counts = src["reduced"].value_counts()
    dom_red = int(red_counts.index[0]) if not red_counts.empty else 0
    dom_red_pct = red_counts.iloc[0] / len(src) if not red_counts.empty else 0

    # Phase alert
    if phase == "IMMINENT":
        st.error(f"SIGNAL PHASE: {phase} — {alert_msg}")
    elif phase == "TRIGGER":
        st.warning(f"SIGNAL PHASE: {phase} — {alert_msg}")
    elif phase == "FORMATION":
        st.info(f"SIGNAL PHASE: {phase} — {alert_msg}")
    else:
        st.success(f"SIGNAL PHASE: {phase} — {alert_msg}")

    # Metrics row
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Dominant Reduced", f"{dom_red} ({dom_red_pct:.0%})")
    m2.metric("Headlines Scanned", len(src))
    m3.metric("HIGH Signals", int((src["score"] >= 70).sum()))
    m4.metric("Sources Active", src["source"].nunique() if "source" in src.columns else 1)

    # Entities + Clusters + Source Breakdown
    ec1, ec2, ec3 = st.columns(3)
    with ec1:
        st.write("**Top Entities**")
        if entities:
            for kw, cnt in entities:
                st.write(f"- {kw}: {cnt}")
        else:
            st.write("- None detected")
    with ec2:
        st.write("**Clusters**")
        if clusters:
            for cl in clusters:
                st.write(f"- {cl['topic']}: {cl['count']}")
        else:
            st.write("- None detected")
    with ec3:
        st.write("**Source Breakdown**")
        if "source" in src.columns:
            for s, c in src["source"].value_counts().items():
                st.write(f"- {s}: {c}")

    st.divider()

if just_scanned:
    st.caption("Today's headlines were auto-scanned on load.")

# --- Tabs ---
tab_today, tab_history, tab_patterns, tab_control = st.tabs(
    ["Today's Scan", "History", "Patterns", "Control Group"]
)

# ===================== TAB 1: TODAY'S SCAN =====================
with tab_today:
    st.header("Today's Scan")

    # Manual scan
    col1, col2 = st.columns([3, 1])
    with col1:
        text_input = st.text_input("Manual scan (headline, name, phrase):", key="scan_text")
    with col2:
        date_input = st.date_input("Date:", value=date.today(), key="scan_date")

    if st.button("Scan", key="scan_btn"):
        if text_input.strip():
            ov = ordinal_gematria(text_input)
            rv = reduce_number(ov)
            existing = load_log()
            sc, label, reasons = signal_score(ov, rv, date_input, existing)
            save_entry(text_input, date_input, ov, rv, sc, "manual")
            c1, c2, c3 = st.columns(3)
            c1.metric("Ordinal", ov)
            c2.metric("Reduced", rv)
            c3.metric("Score", f"{sc}/100")
            if sc >= 70:
                st.error(f"**{label}**")
            elif sc >= 40:
                st.warning(f"**{label}**")
            else:
                st.info(f"**{label}**")
            st.write("**Why this score?**")
            for r in reasons:
                st.write(f"- {r}")

    # Refresh button
    st.divider()
    if st.button("Refresh Today's Data", key="refresh_btn"):
        # Clear today's entries and re-fetch
        full_log = load_log()
        keep = full_log[full_log["date"].astype(str) != today_str]
        keep.to_csv(LOG_FILE, index=False)
        auto_scan_today()
        st.success("Re-fetched today's headlines. Reload the page to see updated data.")

    # Today's results table
    st.divider()
    st.subheader("Today's Headlines")
    today_refresh = load_log()
    today_show = today_refresh[today_refresh["date"].astype(str) == today_str]
    if not today_show.empty:
        # HIGH signal alerts
        high_df = today_show[today_show["score"] >= 70]
        for _, row in high_df.iterrows():
            st.error(f"**HIGH SIGNAL** — {row['text']} (Score: {row['score']}, Source: {row.get('source', '?')})")

        st.dataframe(
            today_show[["text", "ordinal", "reduced", "score", "source"]].sort_values("score", ascending=False),
            use_container_width=True,
        )
    else:
        st.caption("No headlines for today yet.")

    # Cross-source matches
    st.divider()
    st.subheader("Cross-Source Matches")
    st.caption("Headlines from DIFFERENT sources sharing the same ordinal or reduced value today.")
    xmatches = find_cross_source_matches(today_show if not today_show.empty else pd.DataFrame())
    if xmatches:
        for xm in xmatches:
            st.write(
                f"**{xm['type'].title()} {xm['value']}** — shared by: {', '.join(xm['sources'])}"
            )
            for h in xm["headlines"]:
                st.write(f"  - {h}")
    else:
        st.caption("No cross-source matches detected today.")

    # Daily comparison
    st.divider()
    st.subheader("Today vs Yesterday")
    full = load_log()
    if not full.empty:
        yest_str = str(date.today() - timedelta(days=1))
        yest_df = full[full["date"].astype(str) == yest_str]
        td = full[full["date"].astype(str) == today_str]

        if not yest_df.empty and not td.empty:
            yest_dom = int(yest_df["reduced"].value_counts().index[0])
            today_dom = int(td["reduced"].value_counts().index[0])

            dc1, dc2 = st.columns(2)
            dc1.metric("Yesterday's Dominant Reduced", yest_dom)
            dc2.metric("Today's Dominant Reduced", today_dom,
                        delta="changed" if today_dom != yest_dom else "same")

            # Repeating ordinals across days
            shared_ords = set(yest_df["ordinal"]).intersection(set(td["ordinal"]))
            if shared_ords:
                st.write(f"**Repeating ordinals across both days:** {', '.join(str(x) for x in sorted(shared_ords))}")
            else:
                st.write("No ordinals repeat across yesterday and today.")

            st.write(f"Yesterday: {len(yest_df)} entries, Today: {len(td)} entries")
        elif yest_df.empty:
            st.caption("No data from yesterday to compare.")
        else:
            st.caption("No data for today yet.")
    else:
        st.caption("No log data available.")


# ===================== TAB 2: HISTORY =====================
with tab_history:
    st.header("Scan History")
    hist_df = load_log()
    if not hist_df.empty:
        st.dataframe(hist_df.sort_index(ascending=False), use_container_width=True)
        st.caption(f"Total entries: {len(hist_df)}")
    else:
        st.caption("No entries yet.")

    # 30-day trend
    st.divider()
    st.subheader("30-Day Trend")
    if not hist_df.empty:
        cutoff = str(date.today() - timedelta(days=30))
        recent = hist_df[hist_df["date"].astype(str) >= cutoff]
        if not recent.empty:
            dates = recent["date"].astype(str).unique()
            day_dominants = {}
            for d in sorted(dates):
                day_data = recent[recent["date"].astype(str) == d]
                dom = int(day_data["reduced"].value_counts().index[0])
                day_dominants[d] = dom

            dom_counter = Counter(day_dominants.values())
            st.write(f"**Data spans {len(dates)} day(s) in the last 30 days**")
            for val, cnt in dom_counter.most_common(3):
                st.write(f"- Reduced **{val}** dominated **{cnt}** of {len(dates)} days")

            # Show daily dominant as a simple table
            trend_df = pd.DataFrame(
                [{"Date": d, "Dominant Reduced": v} for d, v in day_dominants.items()]
            )
            st.dataframe(trend_df, use_container_width=True)
        else:
            st.caption("No data in the last 30 days.")
    else:
        st.caption("No log data available.")


# ===================== TAB 3: PATTERNS =====================
with tab_patterns:
    st.header("Pattern Detection")
    pat_df = load_log()

    if not pat_df.empty and len(pat_df) >= 2:
        # Repeating ordinals
        ord_counts = pat_df["ordinal"].value_counts()
        repeats = ord_counts[ord_counts >= 2]
        st.write("**Repeating Ordinals** (appearing 2+ times)")
        if not repeats.empty:
            for val, cnt in repeats.head(15).items():
                st.write(f"- Ordinal **{val}** appeared **{cnt}** times")
        else:
            st.write("- No repeating ordinals yet")

        # Dominant reduced
        red_counts = pat_df["reduced"].value_counts()
        st.write("**Reduced Value Distribution**")
        r1, r2 = st.columns(2)
        r1.metric("Most Common Reduced", int(red_counts.index[0]))
        r2.metric("Frequency", f"{red_counts.iloc[0] / len(pat_df):.0%} ({red_counts.iloc[0]}/{len(pat_df)})")

        # Score distribution
        high_c = (pat_df["score"] >= 70).sum()
        med_c = ((pat_df["score"] >= 40) & (pat_df["score"] < 70)).sum()
        low_c = (pat_df["score"] < 40).sum()
        st.write("**Score Distribution**")
        d1, d2, d3 = st.columns(3)
        d1.metric("HIGH", int(high_c))
        d2.metric("MEDIUM", int(med_c))
        d3.metric("LOW", int(low_c))

        # Gematria signal reading
        st.divider()
        st.subheader("Gematria Signal")
        repeat_count = (ord_counts >= 2).sum()
        top_red = int(red_counts.index[0])
        top_red_pct = red_counts.iloc[0] / len(pat_df)

        if high_c >= 3:
            field = "HIGH COHERENCE"
        elif high_c >= 1:
            field = "MODERATE ACTIVITY"
        elif med_c > low_c:
            field = "LOW-MODERATE ACTIVITY"
        else:
            field = "LOW ACTIVITY"

        obs = []
        if high_c >= 2:
            obs.append(f"Multiple high-value alignments ({high_c} HIGH signals)")
        elif high_c == 1:
            obs.append("Isolated high signal detected")
        else:
            obs.append("No strong numerical alignment")

        if repeat_count >= 3:
            obs.append(f"Repeating ordinal structures ({repeat_count} values repeat)")
        elif repeat_count >= 1:
            obs.append(f"Repetition present ({repeat_count} value(s) repeat)")
        else:
            obs.append("No ordinal repetition")

        if top_red_pct > 0.30:
            obs.append(f"Reduced clustering (reduced {top_red} at {top_red_pct:.0%})")
        elif top_red_pct > 0.20:
            obs.append(f"Mild concentration (reduced {top_red} at {top_red_pct:.0%})")
        else:
            obs.append("Reduced values evenly distributed")

        interp_map = {
            "HIGH COHERENCE": "Pattern convergence — multiple signals aligning",
            "MODERATE ACTIVITY": "Emerging signal — monitor for escalation",
            "LOW-MODERATE ACTIVITY": "Noise with occasional alignment",
            "LOW ACTIVITY": "No meaningful pattern structure detected",
        }
        obs_lines = "\n".join(f"  - {o}" for o in obs)
        st.code(
            f"GEMATRIA SIGNAL\n\n"
            f"Field: {field}\n\n"
            f"{obs_lines}\n\n"
            f"-> Interpretation: {interp_map[field]}",
            language=None,
        )

        # All-time cross-source matches
        st.divider()
        st.subheader("Cross-Source Matches (All Time)")
        all_xm = find_cross_source_matches(pat_df)
        if all_xm:
            for xm in all_xm:
                st.write(f"**{xm['date']} — {xm['type'].title()} {xm['value']}** — {', '.join(xm['sources'])}")
                for h in xm["headlines"]:
                    st.write(f"  - {h}")
        else:
            st.caption("No cross-source matches found.")
    else:
        st.caption("Need at least 2 entries to detect patterns.")


# ===================== TAB 4: CONTROL GROUP =====================
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
        cmp_df = load_log()
        if not cmp_df.empty:
            st.subheader("Your Scans vs Control")
            cc1, cc2 = st.columns(2)
            with cc1:
                st.metric("Your avg ordinal", f"{cmp_df['ordinal'].mean():.1f}")
                st.metric("Control avg ordinal", f"{ctrl_df['ordinal'].mean():.1f}")
            with cc2:
                st.metric("Your avg reduced", f"{cmp_df['reduced'].mean():.1f}")
                st.metric("Control avg reduced", f"{ctrl_df['reduced'].mean():.1f}")

        st.divider()
        st.subheader("Sample Data")
        st.dataframe(ctrl_df.head(20), use_container_width=True)
