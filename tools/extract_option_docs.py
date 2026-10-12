"""Extract option descriptions from a checkout of the BWIPP wiki into app/data/option_docs.json.

    git clone --depth 1 https://github.com/bwipp/postscriptbarcode.wiki.git /tmp/bwipp-wiki
    python tools/extract_option_docs.py /tmp/bwipp-wiki

Symbology pages document options as bullets ("* The **includetext** option should normally be
supplied.") and list the encoder ids in their examples ("Encoder: ean13"); those become
encoder-specific descriptions. The option reference pages ("#### inkspread" sections) give the
generic description of every common option.
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys

OUT = os.path.join(os.path.dirname(__file__), "..", "app", "data", "option_docs.json")


def clean(md: str) -> str:
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"\*\*([^*]+)\*\*", r"\1", md)
    md = re.sub(r"(?<!\w)\*([^*\s][^*]*)\*(?!\w)", r"\1", md)
    md = re.sub(r"(?<!\w)_([^_\s][^_]*)_(?!\w)", r"\1", md)
    md = re.sub(r"`([^`]*)`", r"\1", md)
    md = md.replace("**", "").replace("*N*", "N")
    md = re.sub(r"\s+", " ", md).strip()
    return md


def main(wiki: str) -> None:
    per_encoder: dict[str, dict[str, str]] = collections.defaultdict(dict)
    global_docs: dict[str, list[str]] = collections.defaultdict(list)
    for f in sorted(os.listdir(os.path.join(wiki, "symbologies"))):
        if not f.endswith(".md"):
            continue
        md = open(os.path.join(wiki, "symbologies", f), encoding="utf-8").read()
        encoders = sorted(set(re.findall(r"^Encoder:\s*(\S+)", md, re.M)))
        for b in re.findall(r"^\*\s+(.*?)(?=^\*\s|\n\n|\Z)", md, re.M | re.S):
            names = re.findall(r"\*\*([a-z][a-z0-9]*)\*\* option", b)
            if not names:
                continue
            text = clean(b)[:400]
            for n in names:
                for e in encoders:
                    per_encoder[e].setdefault(n, text)
                global_docs[n].append(text)
    for f in sorted(os.listdir(os.path.join(wiki, "options"))):
        if not f.endswith(".md") or f.startswith("_"):
            continue
        md = open(os.path.join(wiki, "options", f), encoding="utf-8").read()
        for m in re.finditer(r"^#{3,5}\s+`?([A-Za-z0-9{}\-]+)`?\s*\n(.*?)(?=^#{2,5}\s|\Z)", md, re.M | re.S):
            name, body = m.group(1), m.group(2)
            para = next((p for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith(("```", "![", "|"))), "")
            text = clean(para)[:400]
            if len(text) < 10:
                continue
            fam = re.match(r"^text\{1-9\}(.+)$", name)
            names = ["text" + fam.group(1), "extratext" + fam.group(1)] if fam else [name.replace("-", "")]
            for n in names:
                global_docs[n].insert(0, text)
    docs = {n: collections.Counter(lst).most_common(1)[0][0] for n, lst in global_docs.items()}
    out = {
        "_source": "https://github.com/bwipp/postscriptbarcode/wiki, extracted by tools/extract_option_docs.py",
        "options": dict(sorted(docs.items())),
        "encoders": {e: dict(sorted(v.items())) for e, v in sorted(per_encoder.items())},
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(len(docs), "option descriptions,", len(per_encoder), "encoders with specific notes ->", os.path.normpath(OUT))


if __name__ == "__main__":
    main(sys.argv[1])
