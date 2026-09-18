#!/usr/bin/env python3
"""Generate the README figures.

GitHub renders README SVGs through <img>, where `currentColor` resolves to the
SVG's own default rather than the page foreground. Theme-adaptive figures
therefore need two files selected with <picture media="(prefers-color-scheme)">.
Both variants are rendered from the same layout code so they cannot drift.

Run:  python3 docs/make_figures.py
"""
from __future__ import annotations

from pathlib import Path

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"
SANS = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

# GitHub's own canvas colours, so the figures sit natively in either theme.
THEMES = {
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "panel2": "#1c2128", "border": "#30363d",
        "text": "#e6edf3", "muted": "#8b949e", "faint": "#6e7681",
        "blue": "#58a6ff", "amber": "#d29922", "green": "#3fb950", "purple": "#bc8cff",
        "amber_bg": "#2b2113", "green_bg": "#12261e", "blue_bg": "#121d2f",
    },
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "panel2": "#eef1f4", "border": "#d0d7de",
        "text": "#1f2328", "muted": "#59636e", "faint": "#818b98",
        "blue": "#0969da", "amber": "#9a6700", "green": "#1a7f37", "purple": "#8250df",
        "amber_bg": "#fff8c5", "green_bg": "#dafbe1", "blue_bg": "#ddf4ff",
    },
}


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, content, *, fill, size=13, family=SANS, anchor="start", weight="400",
         spacing=None, opacity=None):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    extra += f' opacity="{opacity}"' if opacity else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
            f'{esc(content)}</text>')


def box(x, y, w, h, *, fill, stroke, rx=8, width=1, dash=None):
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{width}"{extra}/>')


def pill(x, y, w, h, label, *, fill, stroke, color, size=12):
    """A status chip; text is centred so it survives font substitution."""
    return (box(x, y, w, h, fill=fill, stroke=stroke, rx=h / 2)
            + text(x + w / 2, y + h / 2 + 4, label, fill=color, size=size, family=MONO,
                   anchor="middle", weight="600"))


def arrow(x1, y1, x2, y2, *, stroke, width=1.6, marker="arrow", dash=None):
    extra = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
            f'stroke-width="{width}" marker-end="url(#{marker})"{extra}/>')


def defs(theme: dict[str, str]) -> str:
    def marker(name, color):
        return (f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                f'markerHeight="6" orient="auto-start-reverse">'
                f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>')
    return ("<defs>"
            + marker("arrow", theme["muted"])
            + marker("arrowAccent", theme["blue"])
            + marker("arrowFaint", theme["faint"])
            + "</defs>")


# --------------------------------------------------------------------- hero
# Both figures are drawn at ~880px, GitHub's README content width, so their text
# renders close to 1:1 instead of being downscaled into illegibility.
def hero(theme: dict[str, str]) -> str:
    W, H = 880, 332
    t = theme
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
             f'height="{H}" role="img" aria-label="peerreview: Reviewer 3 changes position on '
             f'ISSUE-002 from NEW ANALYSIS REQUIRED to EXPOSITION ONLY after Reviewer 2 argues '
             f'in message M12 that the proposed analysis identifies a decomposition rather than '
             f'the mechanism. The influence path Reviewer2 to Reviewer3 to Author to Editor '
             f'decision is recorded.">']
    parts.append(defs(t))
    parts.append(f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>')

    parts.append(text(40, 68, "peerreview", fill=t["text"], size=40, family=MONO, weight="700",
                      spacing="-1"))
    parts.append(text(40, 96, "a five-agent editorial board that argues about your manuscript,",
                      fill=t["muted"], size=14.5, family=SANS))
    parts.append(text(40, 117, "then hands you a revision plan instead of another referee report.",
                      fill=t["muted"], size=14.5, family=SANS))

    # One recorded deliberation moment, which is what the whole system is for.
    px, py, pw, ph = 40, 142, 800, 144
    parts.append(box(px, py, pw, ph, fill=t["panel"], stroke=t["border"]))
    parts.append(text(px + 24, py + 28, "ISSUE-002", fill=t["blue"], size=12, family=MONO,
                      weight="700"))
    parts.append(text(px + 104, py + 28, "mechanism claim is stronger than the evidence supports",
                      fill=t["muted"], size=12.5, family=SANS))

    parts.append(text(500, py + 58,
                      "\u201cthat analysis identifies a decomposition, not the mechanism\u201d",
                      fill=t["blue"], size=12, family=SANS, anchor="middle"))
    row = py + 80
    parts.append(text(px + 24, row + 4, "Reviewer3", fill=t["text"], size=12, family=MONO,
                      weight="600"))
    parts.append(pill(140, row - 13, 178, 26, "NEW ANALYSIS REQUIRED",
                      fill=t["amber_bg"], stroke=t["amber"], color=t["amber"], size=10.5))
    parts.append(arrow(332, row, 668, row, stroke=t["blue"], marker="arrowAccent", width=2))
    parts.append(text(500, row + 18, "@Reviewer2, message M12", fill=t["faint"], size=10.5,
                      family=MONO, anchor="middle"))
    parts.append(pill(680, row - 13, 134, 26, "EXPOSITION ONLY",
                      fill=t["green_bg"], stroke=t["green"], color=t["green"], size=10.5))

    parts.append(f'<line x1="{px + 24}" y1="{py + 114}" x2="{px + pw - 24}" y2="{py + 114}" '
                 f'stroke="{t["border"]}" stroke-width="1"/>')
    parts.append(text(px + 24, py + 132, "influence path", fill=t["faint"], size=11, family=SANS))
    parts.append(text(px + 120, py + 132,
                      "Reviewer2 \u2192 Reviewer3 \u2192 Author \u2192 Editor decision",
                      fill=t["purple"], size=11.5, family=MONO, weight="600"))
    parts.append(text(px + pw - 24, py + 132, "no analysis required \u00b7 wording changed",
                      fill=t["faint"], size=11, family=SANS, anchor="end"))

    parts.append(text(40, 318, "independent reviews  \u00b7  issue ledger  \u00b7  shared "
                               "deliberation  \u00b7  recorded position changes  \u00b7  "
                               "disagreements left unresolved",
                      fill=t["faint"], size=11.5, family=SANS))
    parts.append("</svg>")
    return "\n".join(parts)


# ------------------------------------------------------------------ pipeline
def pipeline(theme: dict[str, str]) -> str:
    W, H = 900, 412
    t = theme
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
             f'height="{H}" role="img" aria-label="Three phases. In phase one the three '
             f'reviewers write privately to the editor and have no edges between them. In '
             f'phase two the editor merges their issues into a canonical ledger. In phase '
             f'three every participant can see every message and reviewers argue with each '
             f'other. The ledger drives the revision plan.">']
    parts.append(defs(t))
    parts.append(f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>')

    def header(x, number, label, sub):
        return (text(x, 32, number, fill=t["blue"], size=11, family=MONO, weight="700")
                + text(x + 24, 32, label, fill=t["text"], size=13.5, family=SANS, weight="600")
                + text(x, 52, sub, fill=t["faint"], size=11, family=SANS))

    # -- Phase 1: three contexts that cannot reach each other --------------
    parts.append(header(24, "01", "Independent review", "reviewers cannot see one another"))
    reviewers = [("Reviewer 1", "substantive / theory"),
                 ("Reviewer 2", "methods / identification"),
                 ("Reviewer 3", "generalist / journal")]
    for index, (name, focus) in enumerate(reviewers):
        y = 76 + index * 66
        parts.append(box(54, y, 196, 54, fill=t["panel"], stroke=t["border"], rx=7))
        parts.append(text(70, y + 23, name, fill=t["text"], size=11.5, family=MONO, weight="600"))
        parts.append(text(70, y + 40, focus, fill=t["muted"], size=10.5, family=SANS))
        parts.append(arrow(254, y + 27, 304, y + 27, stroke=t["muted"], width=1.4))

    # The absent edge is the point: no reviewer can reach another.
    for top, bottom in ((103, 169), (169, 235)):
        parts.append(f'<path d="M 46 {top} C 26 {top} 26 {bottom} 46 {bottom}" fill="none" '
                     f'stroke="{t["faint"]}" stroke-width="1.3" stroke-dasharray="4 4"/>')
    for cy in (136, 202):
        parts.append(f'<line x1="28" y1="{cy - 6}" x2="40" y2="{cy + 6}" stroke="{t["faint"]}" '
                     f'stroke-width="1.7"/>')
        parts.append(f'<line x1="40" y1="{cy - 6}" x2="28" y2="{cy + 6}" stroke="{t["faint"]}" '
                     f'stroke-width="1.7"/>')
    parts.append(text(24, 292, "private to the Editor \u00b7 enforced in the", fill=t["faint"],
                      size=10.5, family=SANS))
    parts.append(text(24, 308, "database, not in the prompt", fill=t["faint"], size=10.5,
                      family=SANS))

    # -- Phase 2: consolidation into one canonical ledger -------------------
    parts.append(header(310, "02", "Consolidation", "editor merges, nothing is dropped"))
    parts.append(box(310, 76, 200, 82, fill=t["panel"], stroke=t["border"], rx=7))
    parts.append(text(328, 102, "Editor", fill=t["text"], size=11.5, family=MONO, weight="600"))
    parts.append(text(328, 122, "merges duplicates, keeps", fill=t["muted"], size=10.5,
                      family=SANS))
    parts.append(text(328, 138, "minority concerns, assigns ids", fill=t["muted"], size=10.5,
                      family=SANS))
    parts.append(arrow(410, 162, 410, 190, stroke=t["muted"], width=1.4))

    parts.append(box(310, 196, 200, 150, fill=t["panel2"], stroke=t["blue"], rx=7))
    parts.append(text(328, 222, "ISSUE LEDGER", fill=t["blue"], size=11, family=MONO,
                      weight="700", spacing="0.5"))
    ledger = [("ISSUE-001", "identification"), ("ISSUE-002", "interpretation"),
              ("ISSUE-003", "inference"), ("ISSUE-004", "ext. validity"),
              ("ISSUE-005", "theory")]
    for index, (issue_id, category) in enumerate(ledger):
        y = 248 + index * 21
        parts.append(text(328, y, issue_id, fill=t["text"], size=10.5, family=MONO))
        parts.append(text(400, y, category, fill=t["faint"], size=10.5, family=SANS))
    parts.append(arrow(516, 214, 550, 214, stroke=t["muted"], width=1.4))

    # -- Phase 3: the shared room -----------------------------------------
    parts.append(header(556, "03", "Shared deliberation", "everyone sees every message"))
    parts.append(box(556, 76, 320, 190, fill=t["panel"], stroke=t["border"], rx=7))
    nodes = {
        "Author": (620, 122), "Editor": (812, 122),
        "R1": (598, 212), "R2": (716, 228), "R3": (834, 212),
    }
    order = list(nodes)
    for i, a in enumerate(order):          # everyone can now reach everyone
        for b in order[i + 1:]:
            x1, y1 = nodes[a]
            x2, y2 = nodes[b]
            parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["faint"]}" '
                         f'stroke-width="0.8" opacity="0.45"/>')
    # The one edge worth naming.
    parts.append(arrow(742, 223, 808, 215, stroke=t["blue"], marker="arrowAccent", width=1.8))
    parts.append(text(770, 200, "challenges", fill=t["blue"], size=10.5, family=SANS,
                      anchor="middle"))
    for label, (cx, cy) in nodes.items():
        radius = 26 if label in {"Author", "Editor"} else 21
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{t["panel2"]}" '
                     f'stroke="{t["border"]}" stroke-width="1.2"/>')
        parts.append(text(cx, cy + 4, label, fill=t["text"], size=10.5, family=MONO,
                          anchor="middle", weight="600"))
    parts.append(text(556, 290, "defer \u00b7 challenge \u00b7 withdraw \u00b7 or hold the "
                                "position on the record", fill=t["faint"], size=10.5,
                      family=SANS))

    # -- Output ------------------------------------------------------------
    parts.append(arrow(716, 300, 716, 326, stroke=t["muted"], width=1.4))
    parts.append(box(556, 332, 320, 58, fill=t["green_bg"], stroke=t["green"], rx=7))
    parts.append(text(574, 358, "05_revision_plan.md", fill=t["green"], size=12, family=MONO,
                      weight="700"))
    parts.append(text(574, 377, "what changes \u00b7 what needs analysis \u00b7 what is "
                                "only unclear", fill=t["muted"], size=10.5, family=SANS))
    parts.append("</svg>")
    return "\n".join(parts)


if __name__ == "__main__":
    out = Path(__file__).parent
    for name, render in (("hero", hero), ("pipeline", pipeline)):
        for mode, theme in THEMES.items():
            path = out / f"{name}-{mode}.svg"
            path.write_text(render(theme) + "\n", encoding="utf-8")
            print(f"wrote {path} ({path.stat().st_size} bytes)")
