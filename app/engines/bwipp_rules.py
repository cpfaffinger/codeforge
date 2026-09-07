"""Extract the data-validation rules of every BWIPP encoder from the vendored barcode.ps.

BWIPP validates its input and raises errors such as ``bwipp.ean13badLength`` with a
human readable message ("EAN-13 must be 12 or 13 digits"). Those messages *are* the
format specification of each symbology, and reading them from the library keeps the
documentation in the UI in sync with the engine that enforces it.

treepoem ships BWIPP in its "packaged" form: every encoder is an ASCII85 encoded resource
block containing binary-token PostScript (PLRM 3, section 3.14). String tokens are
``0x8e <len8> bytes`` or ``0x8f <len16> bytes``; error names are literal ``/bwipp.xxx`` names
immediately followed by the message string.
"""

from __future__ import annotations

import base64
import functools
import re
from typing import Any

_BLOCK_RE = re.compile(
    r"%%BeginResource: uk\.co\.terryburton\.bwipp (\S+) [^\n]*\n%%BeginData:[^\n]*\n"
    r"currentfile /ASCII85Decode filter cvx exec\n(.*?)~>",
    re.S,
)
_RULE_RE = re.compile(rb"/bwipp\.([A-Za-z0-9]+)(?:\x8e(.)|\x8f(..))", re.S)

# categories are not part of BWIPP; this grouping follows the BWIPP wiki index
CATEGORIES: dict[str, list[str]] = {
    "Point of sale (EAN / UPC)": ["ean13", "ean8", "ean5", "ean2", "upca", "upce", "isbn", "ismn", "issn", "mands"],
    "Code 128 / Code 39 / Code 93 family": ["code128", "gs1-128", "ean14", "sscc18", "code39", "code39ext", "code32", "pzn", "code93", "code93ext", "hibccode128", "hibccode39"],
    "2 of 5 family": ["interleaved2of5", "itf14", "identcode", "leitcode", "code2of5", "industrial2of5", "iata2of5", "matrix2of5", "coop2of5", "datalogic2of5"],
    "GS1 DataBar": ["databaromni", "databarstacked", "databarstackedomni", "databartruncated", "databarlimited", "databarexpanded", "databarexpandedstacked", "gs1northamericancoupon"],
    "GS1 composite and Digital Link": ["ean13composite", "ean8composite", "upcacomposite", "upcecomposite", "databaromnicomposite", "databarstackedcomposite", "databarstackedomnicomposite", "databartruncatedcomposite", "databarlimitedcomposite", "databarexpandedcomposite", "databarexpandedstackedcomposite", "gs1-128composite", "gs1-cc", "gs1datamatrix", "gs1qrcode", "gs1dotcode", "gs1dldatamatrix", "gs1dlqrcode"],
    "2D matrix codes": ["qrcode", "microqrcode", "rectangularmicroqrcode", "swissqrcode", "datamatrix", "datamatrixrectangular", "datamatrixrectangularextension", "azteccode", "azteccodecompact", "aztecrune", "pdf417", "pdf417compact", "micropdf417", "maxicode", "dotcode", "hanxin", "codeone", "ultracode", "jabcode", "hibcdatamatrix", "hibcqrcode", "hibcpdf417", "hibcmicropdf417", "hibcazteccode", "hibccodablockf"],
    "Stacked and other linear": ["codablockf", "code16k", "code49", "channelcode", "codeone"],
    "Postal codes": ["auspost", "kix", "japanpost", "royalmail", "onecode", "postnet", "planet", "daft", "mailmark", "symbol", "planet"],
    "Pharmaceutical, industrial and legacy": ["pharmacode", "pharmacode2", "code11", "bc412", "rationalizedCodabar", "msi", "plessey", "telepen", "telepennumeric", "posicode", "code11", "raw", "flattermarken", "code2of5"],
}


def _category_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for cat, ids in CATEGORIES.items():
        for i in ids:
            idx.setdefault(i.lower(), cat)
    return idx


def _bwipp_source() -> str:
    import treepoem

    return __import__("importlib").resources.files("treepoem").joinpath("postscriptbarcode/barcode.ps").read_text(encoding="latin-1")


@functools.lru_cache(maxsize=1)
def rules_by_encoder() -> dict[str, list[dict[str, str]]]:
    """encoder id -> [{code, message}] in source order, duplicates removed."""
    result: dict[str, list[dict[str, str]]] = {}
    try:
        src = _bwipp_source()
    except Exception:
        return result
    for name, data in _BLOCK_RE.findall(src):
        try:
            raw = base64.a85decode(re.sub(r"\s", "", data).encode("ascii"), adobe=False)
        except Exception:
            continue
        rules: list[dict[str, str]] = []
        seen: set[str] = set()
        for m in _RULE_RE.finditer(raw):
            code = m.group(1).decode("ascii")
            if m.group(2) is not None:
                n = m.group(2)[0]
                msg = raw[m.end(): m.end() + n]
            else:
                n = (m.group(3)[0] << 8) | m.group(3)[1]
                msg = raw[m.end(): m.end() + n]
            text = msg.decode("latin-1").strip()
            if not text or text in seen or len(text) < 4:
                continue
            seen.add(text)
            rules.append({"code": code, "message": text})
        if rules:
            result[name] = rules
    return result


_LEN_PATTERNS = [
    (re.compile(r"must be (\d+) or (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[2]))),
    (re.compile(r"must be (\d+) to (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[2]))),
    (re.compile(r"must be between (\d+) and (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[2]))),
    (re.compile(r"must be (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[1]))),
    (re.compile(r"must be at least (\d+)"), lambda m: (int(m[1]), None)),
    (re.compile(r"(?:must not exceed|at most|exceed) (\d+)"), lambda m: (None, int(m[1]))),
]


def summarize(rules: list[dict[str, str]]) -> dict[str, Any]:
    """Best-effort structured hints derived from the rule messages."""
    charset = None
    length_hits: list[tuple[int | None, int | None]] = []
    for r in rules:
        low = r["message"].lower()
        if "only digits" in low or "must contain only digits" in low or "non-digit" in low:
            charset = charset or "digits"
        elif "only alphanumeric" in low or "must contain only uppercase" in low:
            charset = charset or "alphanumeric"
        for pat, fn in _LEN_PATTERNS:
            m = pat.search(low)
            if m:
                length_hits.append(fn(m))
                break
    # symbologies made of several fields (postal codes, composites) carry several length rules that
    # refer to different parts of the data; only summarise when there is exactly one, the full list
    # of rules is always shown anyway
    min_len = max_len = None
    if len(length_hits) == 1:
        min_len, max_len = length_hits[0]
    return {"charset": charset, "min_length": min_len, "max_length": max_len}


def wiki_url(description: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", description).strip("-")
    return f"https://github.com/bwipp/postscriptbarcode/wiki/{slug}"


def category_of(bcid: str) -> str:
    return _category_index().get(bcid.lower(), "Other")
