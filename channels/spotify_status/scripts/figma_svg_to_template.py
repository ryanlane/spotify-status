#!/usr/bin/env python3
"""Figma SVG -> Jinja2 template converter

Purpose
-------
Take a raw SVG exported from Figma (with lots of inline styles / attributes)
and produce a Jinja2 `.svg.j2` template consistent with the Spotify Status
channel's existing SVG renderer conventions (see `svg/*.svg.j2`).

Features
--------
1. Consolidate repeated inline `fill`, `stroke`, `font-family`, `font-size`,
   `font-weight` etc. into CSS classes inside a single <style> block.
2. Generate deterministic class names (c1, c2, ...), merging identical style
   declarations.
3. Optionally map certain literal text nodes to dynamic Jinja placeholders
   (e.g. TRACK_NAME, ARTIST_NAME). Mapping provided via JSON file or CLI
   arguments.
4. Inject required root width/height attribute placeholders so template can
   scale based on provided render context variables: `width`, `height`.
5. Provide optional theming substitution: colors that look like dark/light
   backgrounds can be wrapped in Jinja conditional expressions if requested.

Usage
-----
python figma_svg_to_template.py input.svg -o ../svg/new_template.svg.j2 \
  --map track="{{ track_name }}" artist="{{ artist_name }}" album="{{ album_name }}" \
  --theme-substitute "#FFFFFF=({{ '#ffffff' if theme == 'light' else '#111418' }})" \
  --title "Now Playing Portrait"

Minimal:
python figma_svg_to_template.py input.svg -o ../svg/foo.svg.j2

Limitations
-----------
- Does not attempt geometric simplification.
- Only processes <svg>, <g>, <rect>, <path>, <text>, <circle>, <image>, <line>, <polyline>, <polygon>.
- Unknown elements preserved with original attributes (minus style consolidation).

Future Enhancements
-------------------
- Detect palette and auto-propose theme variants.
- Round numeric attributes with tolerance.
- Support splitting portrait/landscape variants by artboard naming.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional

STYLE_ATTRS = [
    "fill",
    "stroke",
    "stroke-width",
    "font-family",
    "font-size",
    "font-weight",
    "text-anchor",
    "opacity",
]

# Regex to split inline style attribute string: key:value;key2:value2;
STYLE_PAIR_RE = re.compile(r"\s*([a-zA-Z\-]+)\s*:\s*([^;]+)\s*;")


def parse_style_attr(style_value: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for m in STYLE_PAIR_RE.finditer(style_value + (";" if not style_value.endswith(";") else "")):
        props[m.group(1)] = m.group(2)
    return props


def extract_relevant_styles(elem: ET.Element) -> Dict[str, str]:
    collected: Dict[str, str] = {}
    # inline style attribute
    style_attr = elem.get("style")
    if style_attr:
        collected.update(parse_style_attr(style_attr))
    # direct attributes
    for attr in STYLE_ATTRS:
        if attr in elem.attrib:
            collected[attr] = elem.attrib[attr]
    # Normalize values (strip whitespace)
    for k, v in list(collected.items()):
        collected[k] = v.strip()
    return collected


def style_dict_to_css(decl: Dict[str, str]) -> str:
    # Keep stable ordering
    keys = sorted(decl.keys())
    return ";".join(f"{k}:{decl[k]}" for k in keys)


def collapse_styles(style_usage: List[Dict[str, str]]) -> Dict[str, str]:
    """Return mapping css_signature -> class_name."""
    mapping: Dict[str, str] = {}
    counter = 1
    for decl in style_usage:
        if not decl:
            continue
        sig = style_dict_to_css(decl)
        if sig not in mapping:
            mapping[sig] = f"c{counter}"
            counter += 1
    return mapping


def apply_classnames(root: ET.Element, mapping: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    """Assign class names to elements, stripping inline styles.

    Returns: element_id -> original style dict (for final consolidated sheet)
    """
    style_reverse: Dict[str, Dict[str, str]] = {}

    def walk(elem: ET.Element):
        decl = extract_relevant_styles(elem)
        if decl:
            sig = style_dict_to_css(decl)
            cls = mapping.get(sig)
            if cls:
                # Append to existing class attr if present
                existing = elem.get("class")
                if existing:
                    if cls not in existing.split():  # avoid duplicates
                        elem.set("class", existing + " " + cls)
                else:
                    elem.set("class", cls)
                # Remove style-bearing attributes
                if "style" in elem.attrib:
                    del elem.attrib["style"]
                for k in STYLE_ATTRS:
                    if k in elem.attrib:
                        del elem.attrib[k]
                style_reverse[cls] = {k: v for k, v in decl.items()}
        for child in list(elem):
            walk(child)

    walk(root)
    return style_reverse


THEME_SUB_RE = re.compile(r"^(?P<color>#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}))=(?P<expr>.+)$")


def apply_theme_substitutions(style_reverse: Dict[str, Dict[str, str]], theme_subs: List[str]):
    if not theme_subs:
        return
    rules: Dict[str, str] = {}
    for item in theme_subs:
        m = THEME_SUB_RE.match(item)
        if not m:
            continue
        rules[m.group("color")] = m.group("expr")
    for cls, decl in style_reverse.items():
        for k, v in list(decl.items()):
            if v in rules:
                decl[k] = rules[v]


@dataclass
class TextMapping:
    replacements: Dict[str, str] = field(default_factory=dict)

    def apply(self, root: ET.Element):
        for elem in root.iter():
            if elem.tag.endswith("text") and elem.text:
                stripped = elem.text.strip()
                if stripped in self.replacements:
                    elem.text = self.replacements[stripped]


def build_style_block(style_reverse: Dict[str, Dict[str, str]]) -> str:
    lines = ["<style><![CDATA["]
    for cls, decl in sorted(style_reverse.items(), key=lambda x: x[0]):
        parts = []
        for k, v in sorted(decl.items()):
            parts.append(f"{k}: {v};")
        lines.append(f".{cls} {{ {' '.join(parts)} }}")
    lines.append("]]>" + "</style>")
    return "\n      ".join(lines)


def ensure_root_dimensions(svg_root: ET.Element):
    # Replace existing numeric width/height with Jinja placeholders
    if "width" in svg_root.attrib:
        svg_root.set("width", "{{ width }}")
    else:
        svg_root.attrib["width"] = "{{ width }}"
    if "height" in svg_root.attrib:
        svg_root.set("height", "{{ height }}")
    else:
        svg_root.attrib["height"] = "{{ height }}"
    # viewBox should remain static; if missing attempt to derive from original width/height
    if "viewBox" not in svg_root.attrib:
        w = svg_root.get("width", "{{ width }}").replace("{{ width }}", "0")
        h = svg_root.get("height", "{{ height }}").replace("{{ height }}", "0")
        svg_root.set("viewBox", f"0 0 {w} {h}")


def serialize_svg(root: ET.Element, style_block: str, title: Optional[str]) -> str:
    # Insert/replace <defs><style>
    defs = None
    for child in list(root):
        if child.tag.endswith("defs"):
            defs = child
            break
    if defs is None:
        defs = ET.Element("defs")
        # place as first child
        root.insert(0, defs)
    # Remove any existing style children
    for existing in list(defs):
        if existing.tag.endswith("style"):
            defs.remove(existing)
    # Inject new style block as raw text (ElementTree escapes by default; we embed placeholder)
    style_elem = ET.fromstring("<dummy />")
    style_elem.text = style_block  # We'll post-process replacement below
    defs.append(style_elem)

    svg_str = ET.tostring(root, encoding="unicode")
    # Remove <dummy> wrappers, restore style block
    svg_str = svg_str.replace("<dummy>", "").replace("</dummy>", "")
    # Pretty minimal tidy
    svg_str = re.sub(r">\s+<", ">\n  <", svg_str)

    header = "{% set W = width %}{% set H = height %}\n"
    comment_title = f"<!-- {title} (generated) -->\n" if title else ""
    return header + comment_title + svg_str


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Convert Figma SVG to Jinja2 template")
    parser.add_argument("input", type=Path, help="Input Figma-exported SVG path")
    # Allow multiple aliases since user muscle-memory used -p previously.
    parser.add_argument("-o", "-p", "--output", "--path", type=Path, required=True, help="Output template path (.svg.j2)")
    parser.add_argument("--map", nargs="*", help="Text node mappings literal=jinja_expr (no spaces around =)")
    parser.add_argument("--map-json", type=Path, help="JSON file with {literal: replacement}")
    parser.add_argument("--theme-substitute", nargs="*", default=[], help="Color substitution rules COLOR=JINJA_EXPR")
    parser.add_argument("--title", help="Comment title to embed at top")

    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1

    tree = ET.parse(args.input)
    root = tree.getroot()

    # Collect styles
    style_usage: List[Dict[str, str]] = []
    for el in root.iter():
        style_usage.append(extract_relevant_styles(el))
    mapping = collapse_styles(style_usage)
    style_reverse = apply_classnames(root, mapping)

    # Text mapping
    replacements: Dict[str, str] = {}
    if args.map:
        for pair in args.map:
            if "=" not in pair:
                continue
            lit, repl = pair.split("=", 1)
            replacements[lit] = repl
    if args.map_json and args.map_json.exists():
        replacements.update(json.loads(args.map_json.read_text(encoding="utf-8")))
    TextMapping(replacements).apply(root)

    # Theme substitutions
    apply_theme_substitutions(style_reverse, args.theme_substitute)

    # Dimensions placeholders
    ensure_root_dimensions(root)

    style_block = build_style_block(style_reverse)
    result = serialize_svg(root, style_block, args.title)

    # Ensure file suffix
    if not str(args.output).endswith(".svg.j2"):
        print("Warning: output should typically end with .svg.j2", file=sys.stderr)

    args.output.write_text(result, encoding="utf-8")
    print(f"Wrote template -> {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
