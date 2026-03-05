#!/usr/bin/env python3
"""Convert Markdown files to PDF using markdown2 + Playwright (Chromium).

Chromium's print-to-PDF gives pixel-perfect rendering of tables, code blocks,
and ASCII art.

Requirements:
    pip install markdown2 playwright
    playwright install chromium

Usage:
    # Convert specific files
    python scripts/md2pdf.py README.md out.pdf
    python scripts/md2pdf.py README.md gateway.pdf client/README.md client.pdf

    # Generate both project PDFs (default when no args)
    python scripts/md2pdf.py

    # Or via make
    make pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown2
from playwright.sync_api import sync_playwright

CSS = """
@page {
    size: letter;
    margin: 0.6in 0.5in;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 12px;
    line-height: 1.55;
    color: #1a1a1a;
    max-width: 100%;
}

/* Headings */
h1 { font-size: 24px; margin: 28px 0 12px; border-bottom: 2px solid #333; padding-bottom: 6px; }
h2 { font-size: 19px; margin: 24px 0 10px; border-bottom: 1px solid #d0d0d0; padding-bottom: 4px; }
h3 { font-size: 15px; margin: 18px 0 8px; }
h4 { font-size: 13px; margin: 14px 0 6px; }

/* Inline code */
code {
    font-family: "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
    font-size: 0.9em;
    background: #f0f0f0;
    padding: 1px 5px;
    border-radius: 3px;
    border: 1px solid #e0e0e0;
}

/* Code blocks — critical for ASCII diagrams */
pre {
    font-family: "SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace;
    font-size: 9px;
    line-height: 1.35;
    background: #f6f8fa;
    padding: 12px 14px;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    white-space: pre;
    overflow-x: visible;
    page-break-inside: avoid;
}
pre code {
    background: none;
    padding: 0;
    border: none;
    font-size: inherit;
}

/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 11px;
    page-break-inside: auto;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th {
    background: #f0f3f6;
    border: 1px solid #c8ccd0;
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    white-space: nowrap;
}
td {
    border: 1px solid #d0d4d8;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
}
/* Let description columns wrap, keep short columns tight */
td:first-child { white-space: nowrap; }
tr:nth-child(even) td { background: #f8f9fa; }

/* Blockquotes */
blockquote {
    border-left: 4px solid #d0d7de;
    margin: 12px 0;
    padding: 4px 16px;
    color: #555;
}

/* Misc */
hr { border: none; border-top: 1px solid #d0d0d0; margin: 20px 0; }
a { color: #0969da; text-decoration: none; }
strong { font-weight: 600; }
ul, ol { padding-left: 24px; }
li { margin: 3px 0; }
"""


def convert(md_path: str, pdf_path: str) -> None:
    """Convert a single Markdown file to PDF."""
    md_text = Path(md_path).read_text()

    html_body = markdown2.markdown(
        md_text,
        extras=[
            "tables",
            "fenced-code-blocks",
            "code-friendly",
            "header-ids",
            "strike",
            "task_list",
        ],
    )

    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(full_html, wait_until="networkidle")
        page.pdf(
            path=pdf_path,
            format="Letter",
            margin={"top": "0.6in", "bottom": "0.6in", "left": "0.5in", "right": "0.5in"},
            print_background=True,
        )
        browser.close()

    print(f"  OK: {pdf_path}")


# Default file pairs when invoked with no arguments
_DEFAULTS = [
    ("README.md", "agentic-primitives-gateway.pdf"),
    ("client/README.md", "agentic-primitives-client.pdf"),
]


def main() -> None:
    args = sys.argv[1:]

    if not args:
        # No arguments — generate both project PDFs
        repo_root = Path(__file__).resolve().parent.parent
        print("Generating project PDFs...")
        for md, pdf in _DEFAULTS:
            md_abs = repo_root / md
            pdf_abs = repo_root / pdf
            if not md_abs.exists():
                print(f"  SKIP: {md} (not found)")
                continue
            convert(str(md_abs), str(pdf_abs))
        return

    if len(args) % 2 != 0:
        print(f"Usage: {sys.argv[0]} [<input.md> <output.pdf> ...]")
        print(f"       {sys.argv[0]}   (no args = generate both project PDFs)")
        sys.exit(1)

    for i in range(0, len(args), 2):
        convert(args[i], args[i + 1])


if __name__ == "__main__":
    main()
