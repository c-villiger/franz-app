"""Französisch-Vokabeltrainer – Streamlit-Dashboard.

Starten:  streamlit run app.py      (oder ./run.sh)
"""

from __future__ import annotations

import inspect
import os
import random
import subprocess
from html import escape

import streamlit as st

from vocabtrainer import config
from vocabtrainer import db as dbmod
from vocabtrainer.config import (
    DIRECTION_LABELS,
    LABEL_EMOJI,
    ROOT,
    VOCAB_FILE,
)
from vocabtrainer.scheduler import pick_card, pick_direction
from vocabtrainer.vocab_file import (
    VocabFileError,
    append_entries,
    load_entries,
    parse_pair_text,
)

st.set_page_config(
    page_title="Französisch-Trainer",
    page_icon="🇫🇷",
    layout="centered",
    initial_sidebar_state="auto",
)

# Streamlit-APIs unterscheiden sich zwischen Versionen; hier einmal pruefen,
# damit das Dashboard auch mit aelteren Installationen laeuft.
_BTN_PARAMS = inspect.signature(st.button).parameters
_SUPPORTS_SHORTCUT = "shortcut" in _BTN_PARAMS
_SUPPORTS_WIDTH = "width" in _BTN_PARAMS


def wide_button(label: str, *, key: str, kind: str = "secondary", shortcut: str | None = None):
    kwargs: dict = {"key": key, "type": kind}
    if _SUPPORTS_WIDTH:
        kwargs["width"] = "stretch"
    else:  # pragma: no cover - alte Streamlit-Versionen
        kwargs["use_container_width"] = True
    if shortcut and _SUPPORTS_SHORTCUT:
        kwargs["shortcut"] = shortcut
    return st.button(label, **kwargs)


def keyed_container(key: str):
    """``st.container`` mit CSS-Klasse ``st-key-<key>`` (falls unterstuetzt)."""
    try:
        return st.container(key=key)
    except TypeError:  # pragma: no cover - aeltere Streamlit-Versionen
        return st.container()


CSS = """
<style>
  .block-container { padding-top: 2.2rem; max-width: 46rem; }
  .franz-card {
      border: 1px solid rgba(128,128,128,.28);
      border-radius: 18px;
      padding: 2.4rem 1.4rem;
      text-align: center;
      background: rgba(128,128,128,.06);
      margin: .6rem 0 1rem 0;
  }
  .franz-direction {
      font-size: .82rem; letter-spacing: .06em; text-transform: uppercase;
      opacity: .65; margin-bottom: .9rem;
  }
  .franz-prompt { font-size: 2rem; font-weight: 650; line-height: 1.25; }
  .franz-answer {
      font-size: 1.45rem; margin-top: 1.1rem; padding-top: 1.1rem;
      border-top: 1px dashed rgba(128,128,128,.35); opacity: .95;
  }
  .franz-meta { font-size: .82rem; opacity: .6; margin-top: 1rem; }
  .franz-stats { display: flex; flex-wrap: wrap; gap: .5rem; margin: .2rem 0 .9rem 0; }
  .franz-stat {
      flex: 1 1 7.5rem; padding: .55rem .7rem; border-radius: 12px;
      border: 1px solid rgba(128,128,128,.25);
      border-left: 4px solid var(--c);
      background: rgba(128,128,128,.05);
  }
  .franz-stat-value { font-size: 1.7rem; font-weight: 700; line-height: 1.1; }
  .franz-stat-label { font-size: .78rem; opacity: .72; white-space: nowrap; }
  .franz-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: .2rem 0 1rem 0; }
  .franz-bar span { display: block; height: 100%; }
  /* Die drei Bewertungsknoepfe etwas groesser und farblich zugeordnet. */
  .st-key-b_sicher button, .st-key-b_mittel button, .st-key-b_unsicher button {
      height: 3.1rem; font-size: 1.02rem; font-weight: 600;
  }
  .st-key-b_sicher button:hover   { border-color: #3fb950; color: #3fb950; }
  .st-key-b_mittel button:hover   { border-color: #e3b341; color: #e3b341; }
  .st-key-b_unsicher button:hover { border-color: #e5534b; color: #e5534b; }
  .st-key-reveal button { height: 3rem; }
  @media (max-width: 640px) {
      .franz-prompt { font-size: 1.55rem; }
      .franz-card { padding: 1.8rem 1rem; }
      .franz-stat-value { font-size: 1.4rem; }
      .block-container h1 { font-size: 1.7rem; }
      .block-container { padding-top: 1.4rem; }
  }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

BAR_COLORS = {
    "unsicher": "#e5534b",
    "mittel": "#e3b341",
    "sicher": "#3fb950",
    "unseen": "#8b949e",
}


def _from_secrets(key: str) -> str:
    """Wert aus `.streamlit/secrets.toml` bzw. den Secrets der Streamlit-Cloud."""
    try:
        return str(st.secrets.get(key, "")).strip()
    except Exception:  # keine Secrets hinterlegt -> lokaler Betrieb
        return ""


# Lokal kommt die Konfiguration aus der Umgebung, gehostet aus den Secrets.
DB_URL = os.environ.get("FRANZ_DB_URL", "").strip() or _from_secrets("db_url")
DB_TOKEN = os.environ.get("FRANZ_DB_TOKEN", "").strip() or _from_secrets("db_token")

# Auf einem Server ist das Dateisystem flüchtig: dort dürfen Vokabeln nur über
# das Repo dazukommen, sonst überlebt die Karte in der Datenbank, ihre Zeile in
# der Datei aber nicht - und der nächste Abgleich deaktiviert sie wieder.
VOCAB_READONLY = config.vocab_readonly(DB_URL)


@st.cache_resource
def get_conn(url: str, token: str):
    conn = dbmod.connect(url=url, token=token)
    dbmod.sync_from_file(conn, load_entries())
    return conn


conn = get_conn(DB_URL, DB_TOKEN)
ss = st.session_state
ss.setdefault("current_id", None)
ss.setdefault("direction", "fr2de")
ss.setdefault("revealed", False)
ss.setdefault("recent", [])          # zuletzt gezeigte Karten-IDs (neueste zuerst)
ss.setdefault("mode", "mixed")       # Abfragerichtung
ss.setdefault("tag_filter", [])
ss.setdefault("session_count", 0)
ss.setdefault("flash", None)
ss.setdefault("rng", random.Random())


def deck() -> list:
    return dbmod.all_cards(conn, tags=ss.tag_filter or None)


def next_card(cards) -> None:
    """Zieht die naechste Karte und setzt die Anzeige zurueck."""
    card = pick_card(cards, recent_ids=ss.recent, rng=ss.rng)
    ss.current_id = card.id if card else None
    ss.direction = pick_direction(ss.mode, ss.rng)
    ss.revealed = False
    if card:
        ss.recent = [card.id, *[i for i in ss.recent if i != card.id]][:12]


def rate(card_id: int, label: str, cards) -> None:
    dbmod.set_label(conn, card_id, label, ss.direction)
    ss.session_count += 1
    ss.flash = f"{LABEL_EMOJI[label]} als *{label}* gespeichert"
    next_card(cards)


# ---------------------------------------------------------------- Seitenleiste
with st.sidebar:
    st.header("Einstellungen")
    modes = ["mixed", "fr2de", "de2fr"]
    ss.mode = st.radio(
        "Abfragerichtung",
        modes,
        index=modes.index(ss.mode),
        format_func=lambda m: DIRECTION_LABELS[m],
    )
    tags = dbmod.all_tags(conn)
    if tags:
        ss.tag_filter = st.multiselect("Nur diese Tags", tags, default=ss.tag_filter)

    st.divider()
    st.subheader("Vokabeln hinzufügen")
    if VOCAB_READONLY:
        st.caption(
            "Diese Instanz läuft gehostet – neue Vokabeln kommen über das Repo "
            f"(`{VOCAB_FILE.relative_to(ROOT)}`) dazu. Nach dem Push erscheinen "
            "sie beim nächsten Deploy automatisch hier."
        )
    else:
        with st.form("add_form", clear_on_submit=True):
            raw = st.text_area(
                "Eine Vokabel pro Zeile",
                placeholder="avoir le cafard | Trübsal blasen | idiom\nposer un lapin | jemanden versetzen",
                height=120,
            )
            submitted = st.form_submit_button("Hinzufügen")
        if submitted and raw.strip():
            try:
                entries = parse_pair_text(raw)
                result = append_entries(entries)
                sync = dbmod.sync_from_file(conn, load_entries())
                st.success(f"{result.n_added} hinzugefügt, {len(result.duplicates)} Duplikate. {sync.summary()}")
            except VocabFileError as exc:
                st.error(str(exc))

    st.divider()
    st.subheader("Synchronisieren")
    st.caption(f"Vokabeldatei: `{VOCAB_FILE.relative_to(ROOT)}`")
    st.caption(
        "Fortschritt: gehostete Datenbank" if DB_URL else "Fortschritt: lokale Datei"
    )
    if st.button("Datei → Datenbank"):
        st.success(dbmod.sync_from_file(conn, load_entries()).summary())
    if not VOCAB_READONLY and st.button("git pull (neue Wörter vom Handy holen)"):
        try:
            out = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=60,
            )
            (st.success if out.returncode == 0 else st.error)(
                (out.stdout + out.stderr).strip() or "fertig"
            )
            if out.returncode == 0:
                st.info(dbmod.sync_from_file(conn, load_entries()).summary())
        except Exception as exc:  # pragma: no cover - Umgebungsfehler
            st.error(f"git pull fehlgeschlagen: {exc}")

    st.divider()
    with st.expander("Fortschritt zurücksetzen"):
        st.caption("Löscht alle Labels und die Historie. Die Vokabeln bleiben.")
        if st.button("Ja, alles zurücksetzen", type="primary"):
            dbmod.reset_progress(conn)
            ss.current_id, ss.recent, ss.session_count = None, [], 0
            st.success("Fortschritt zurückgesetzt.")

    st.divider()
    st.subheader("Letzte Bewertungen")
    for row in dbmod.recent_reviews(conn, 8):
        st.caption(f"{LABEL_EMOJI[row['label']]} {row['fr']} → {row['de']}")


# -------------------------------------------------------------------- Kopfzeile
cards = deck()
counts = dbmod.stats(conn, cards)

st.title("🇫🇷 Französisch-Trainer")

# Zaehlerzeile als eigenes HTML statt st.columns: bricht auf dem Handy
# sauber auf zwei Zeilen um, statt vier schmale Spalten zu quetschen.
tiles = "".join(
    f'<div class="franz-stat" style="--c:{BAR_COLORS[key]}">'
    f'<div class="franz-stat-value">{counts[key]}</div>'
    f'<div class="franz-stat-label">{label}</div></div>'
    for key, label in (
        ("sicher", "🟢 sicher"),
        ("mittel", "🟡 mittel"),
        ("unsicher", "🔴 unsicher"),
        ("unseen", "⚪️ noch nicht gefragt"),
    )
)
total = counts["total"] or 1
segments = "".join(
    f'<span style="width:{counts[key] / total * 100:.4f}%;background:{BAR_COLORS[key]}"></span>'
    for key in ("sicher", "mittel", "unsicher", "unseen")
    if counts[key]
)
st.markdown(
    f'<div class="franz-stats">{tiles}</div>'
    f'<div class="franz-bar">{segments}</div>',
    unsafe_allow_html=True,
)
st.caption(
    f"{counts['total']} Vokabeln · heute bewertet: {dbmod.reviews_today(conn)}"
    f" · diese Sitzung: {ss.session_count}"
)

if ss.flash:
    st.toast(ss.flash)
    ss.flash = None

# ------------------------------------------------------------------------ Karte
if not cards:
    st.info(
        "Keine Vokabeln vorhanden (oder der Tag-Filter passt auf nichts). "
        "Füge links in der Seitenleiste welche hinzu."
    )
    st.stop()

card = dbmod.get_card(conn, ss.current_id) if ss.current_id else None
if card is None or card.id not in {c.id for c in cards}:
    # Erste Karte, oder die aktuelle passt nicht mehr zum Tag-Filter.
    next_card(cards)
    card = dbmod.get_card(conn, ss.current_id)

# Richtung in der Seitenleiste umgestellt -> sofort auf diese Karte anwenden.
if ss.mode != "mixed" and ss.direction != ss.mode:
    ss.direction = ss.mode
    ss.revealed = False

# Ohne Leerzeilen zusammenbauen: Markdown wuerde einen HTML-Block sonst nach
# der ersten Leerzeile beenden und den Rest als Text ausgeben.
answer_html = (
    f'<div class="franz-answer">{escape(card.answer(ss.direction))}</div>'
    if ss.revealed
    else ""
)
st.markdown(
    '<div class="franz-card">'
    f'<div class="franz-direction">{escape(DIRECTION_LABELS[ss.direction])}</div>'
    f'<div class="franz-prompt">{escape(card.prompt(ss.direction))}</div>'
    f"{answer_html}</div>",
    unsafe_allow_html=True,
)

if not ss.revealed:
    if wide_button("👁️ Antwort zeigen", key="reveal", shortcut="space"):
        ss.revealed = True
        st.rerun()
else:
    st.caption("Wie sicher warst du?")

with keyed_container("rating"):
    b1, b2, b3 = st.columns(3)
    with b1:
        if wide_button("🟢 sicher", key="b_sicher", shortcut="1"):
            rate(card.id, "sicher", cards)
            st.rerun()
    with b2:
        if wide_button("🟡 mittel", key="b_mittel", shortcut="2"):
            rate(card.id, "mittel", cards)
            st.rerun()
    with b3:
        if wide_button("🔴 unsicher", key="b_unsicher", shortcut="3"):
            rate(card.id, "unsicher", cards)
            st.rerun()

s1, s2 = st.columns([1, 1])
with s1:
    if wide_button("⏭️ Überspringen", key="skip"):
        dbmod.mark_skipped(conn, card.id)
        next_card(cards)
        st.rerun()
with s2:
    if wide_button("🔁 Richtung drehen", key="flip"):
        ss.direction = "de2fr" if ss.direction == "fr2de" else "fr2de"
        ss.revealed = False
        st.rerun()

meta = [
    f"{LABEL_EMOJI[card.label]} {card.label or 'noch nicht gefragt'}",
    f"{card.times_asked}× gefragt",
]
if card.tags:
    meta.append(" · ".join(card.tags))
note_html = f"<br>{escape(card.note)}" if card.note else ""
st.markdown(
    f'<div class="franz-meta">{escape(" · ".join(meta))}{note_html}</div>',
    unsafe_allow_html=True,
)
