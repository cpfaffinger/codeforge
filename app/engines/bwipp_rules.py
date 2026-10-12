"""Extract per-encoder metadata from the vendored BWIPP ``barcode.ps``: validation rules, examples, options.

BWIPP validates its input and raises errors such as ``bwipp.ean13badLength`` with a
human readable message ("EAN-13 must be 12 or 13 digits"). Those messages *are* the
format specification of each symbology, and reading them from the library keeps the
documentation in the UI in sync with the engine that enforces it.

treepoem ships BWIPP in its "packaged" form: every encoder is an ASCII85 encoded resource
block containing binary-token PostScript (PLRM 3, section 3.14). String tokens are
``0x8e <len8> bytes`` or ``0x8f <len16> bytes``, numbers ``0x88 <int8>``, ``0x86 <int16>``,
``0x84 <int32>``, ``0x8a <ieee float>``; names and ``true``/``false`` stay ASCII and the
system name ``def`` is the token ``0x92 0x33``. Error names are literal ``/bwipp.xxx`` names
immediately followed by the message string.

Every encoder starts with its option defaults (``/eclevel (unset) def /parse false def ...``)
before it calls ``processoptions``, which coerces the caller's values to the type of the
default. The defaults therefore tell us the option names, their types and defaults; the
``--REQUIRES`` header lists the encoders an encoder delegates to (``d3aqr`` -> ``qrcode``),
whose options are accepted as well. Each encoder header also carries ``--EXAM``/``--EXOP``,
BWIPP's own example data and options.
"""

from __future__ import annotations

import base64
import functools
import json
import re
import struct
from pathlib import Path
from typing import Any

_DOCS_FILE = Path(__file__).resolve().parent.parent / "data" / "option_docs.json"

_BLOCK_RE = re.compile(
    r"%%BeginResource: uk\.co\.terryburton\.bwipp (\S+) [^\n]*\n%%BeginData:[^\n]*\n"
    r"currentfile /ASCII85Decode filter cvx exec\n(.*?)~>",
    re.S,
)
_RULE_RE = re.compile(rb"/bwipp\.([A-Za-z0-9]+)(?:\x8e(.)|\x8f(..))", re.S)
_HEADER_RE = re.compile(r"% --BEGIN ENCODER (\S+)--\n((?:% --[A-Z]+:? ?[^\n]*\n)+)")
_HEADER_LINE_RE = re.compile(r"% --([A-Z]+):? ?(.*?)(?:--)?$")
_DEFAULT_RE = re.compile(rb"/([A-Za-z_][A-Za-z0-9_]*)(?: ?(true|false)|\x88(.)|\x86(..)|\x84(....)|\x8a(....)|\x8e(.))", re.S)
_DEF_TOKEN = b"\x923"

# categories are not part of BWIPP; this grouping follows the BWIPP wiki index
CATEGORIES: dict[str, list[str]] = {
    "Point of sale (EAN / UPC)": ["ean13", "ean8", "ean5", "ean2", "upca", "upce", "isbn", "ismn", "issn", "mands"],
    "Code 128 / Code 39 / Code 93 family": ["code128", "gs1-128", "ean14", "sscc18", "code39", "code39ext", "code32", "pzn", "code93", "code93ext", "hibccode128", "hibccode39"],
    "2 of 5 family": ["interleaved2of5", "itf14", "identcode", "leitcode", "code2of5", "industrial2of5", "iata2of5", "matrix2of5", "coop2of5", "datalogic2of5"],
    "GS1 DataBar": ["databaromni", "databarstacked", "databarstackedomni", "databartruncated", "databarlimited", "databarexpanded", "databarexpandedstacked", "gs1northamericancoupon"],
    "GS1 composite and Digital Link": ["ean13composite", "ean8composite", "upcacomposite", "upcecomposite", "databaromnicomposite", "databarstackedcomposite", "databarstackedomnicomposite", "databartruncatedcomposite", "databarlimitedcomposite", "databarexpandedcomposite", "databarexpandedstackedcomposite", "gs1-128composite", "gs1-cc", "gs1datamatrix", "gs1datamatrixrectangular", "gs1qrcode", "gs1dotcode", "gs1dldatamatrix", "gs1dlqrcode", "d3aqr"],
    "2D matrix codes": ["qrcode", "microqrcode", "rectangularmicroqrcode", "swissqrcode", "datamatrix", "datamatrixrectangular", "datamatrixrectangularextension", "azteccode", "azteccodecompact", "aztecrune", "pdf417", "pdf417compact", "micropdf417", "maxicode", "dotcode", "hanxin", "codeone", "ultracode", "jabcode"],
    "HIBC (health industry)": ["hibccode128", "hibccode39", "hibcdatamatrix", "hibcdatamatrixrectangular", "hibcqrcode", "hibcpdf417", "hibcmicropdf417", "hibcazteccode", "hibccodablockf"],
    "Stacked and other linear": ["codablockf", "code16k", "code49", "channelcode"],
    "Postal codes": ["auspost", "kix", "japanpost", "royalmail", "onecode", "postnet", "planet", "daft", "mailmark", "symbol"],
    "Pharmaceutical, industrial and legacy": ["pharmacode", "pharmacode2", "code11", "bc412", "rationalizedCodabar", "msi", "plessey", "telepen", "telepennumeric", "posicode", "raw", "flattermarken"],
}

# option names BWIPP uses internally; they are not caller options
_INTERNAL = {
    "mag", "xdim", "xnom", "xmin", "xmax", "hdim", "hnom", "ast", "e", "bits", "modunit", "dontdraw",
    "strictspec", "propspec", "loosespec", "j", "v", "k", "lintype", "linwidth", "nwidth", "swidth", "wwidth",
    "barxmult", "rowmult", "sepheight", "rowheight", "colormap", "start", "rev", "pnative", "fixedeclevel", "newencoder",
}
# BWIPP's own aliases (processoptions.aliases): internal name -> public option name
_ALIASES = {
    "text1": "alttext", "text1size": "textsize", "text1xoffset": "textxoffset", "text1yoffset": "textyoffset",
    "text1font": "textfont", "text1xalign": "textxalign", "text1yalign": "textyalign", "text1gaps": "textgaps",
    "text1color": "textcolor", "text2": "extratext", "text2font": "extratextfont", "text2size": "extratextsize",
    "text2xalign": "extratextxalign", "text2yalign": "extratextyalign", "text2xoffset": "extratextxoffset",
    "text2yoffset": "extratextyoffset", "text2color": "extratextcolor", "text2gaps": "extratextgaps",
}

# descriptions follow the BWIPP options reference (https://github.com/bwipp/postscriptbarcode/wiki/Options-Reference)
OPTION_DOCS: dict[str, tuple[str, str]] = {
    # (group, description)
    "includetext": ("text", "Show the human readable text under (or beside) the symbol."),
    "textfont": ("text", "PostScript font for the human readable text, e.g. Helvetica, Courier, OCR-B."),
    "textsize": ("text", "Font size of the human readable text in points."),
    "textgaps": ("text", "Extra space between characters of the text in points."),
    "textxalign": ("text", "Horizontal alignment of the text."),
    "textyalign": ("text", "Vertical position of the text."),
    "textxoffset": ("text", "Shift the text horizontally in points."),
    "textyoffset": ("text", "Shift the text vertically in points."),
    "textcolor": ("text", "Colour of the text as RRGGBB (or CMYK hex)."),
    "alttext": ("text", "Print this text instead of the encoded data."),
    "extratext": ("text", "A second line of text."),
    "extratextfont": ("text", "Font of the extra text."),
    "extratextsize": ("text", "Size of the extra text in points."),
    "extratextxalign": ("text", "Horizontal alignment of the extra text."),
    "extratextyalign": ("text", "Vertical position of the extra text."),
    "extratextxoffset": ("text", "Shift the extra text horizontally in points."),
    "extratextyoffset": ("text", "Shift the extra text vertically in points."),
    "extratextcolor": ("text", "Colour of the extra text as RRGGBB."),
    "extratextgaps": ("text", "Extra space between characters of the extra text."),
    "addontextfont": ("text", "Font of the EAN/UPC add-on text."),
    "addontextsize": ("text", "Size of the add-on text in points."),
    "addontextxoffset": ("text", "Shift the add-on text horizontally."),
    "addontextyoffset": ("text", "Shift the add-on text vertically."),
    "addongap": ("appearance", "Gap between the main symbol and the EAN/UPC add-on in points."),
    "addondescent": ("appearance", "How far the add-on bars extend below the main bars."),
    "height": ("appearance", "Bar height in inches (linear) or symbol height for 2D symbols."),
    "width": ("appearance", "Overall symbol width in inches; 0 = natural width."),
    "inkspread": ("appearance", "Compensate for ink spread: shrink (positive) or widen (negative) every element, in modules."),
    "inkspreadh": ("appearance", "Horizontal ink spread compensation."),
    "inkspreadv": ("appearance", "Vertical ink spread compensation."),
    "showborder": ("appearance", "Draw a border around the symbol."),
    "borderwidth": ("appearance", "Width of the border in points."),
    "borderleft": ("appearance", "Quiet zone / border on the left in points."),
    "borderright": ("appearance", "Quiet zone / border on the right in points."),
    "bordertop": ("appearance", "Quiet zone / border above in points."),
    "borderbottom": ("appearance", "Quiet zone / border below in points."),
    "bordercolor": ("appearance", "Border colour as RRGGBB."),
    "barcolor": ("appearance", "Colour of the bars/modules as RRGGBB."),
    "backgroundcolor": ("appearance", "Background colour as RRGGBB."),
    "color": ("appearance", "Colour of the modules as RRGGBB (Ultracode/DotCode)."),
    "dotty": ("appearance", "Render the modules as dots instead of squares (2D symbols)."),
    "guardwhitespace": ("appearance", "Show the < > quiet-zone indicators of EAN/UPC."),
    "guardwidth": ("appearance", "Width of the quiet-zone indicators in points."),
    "guardheight": ("appearance", "Height of the quiet-zone indicators in points."),
    "guardleftpos": ("appearance", "Position of the left quiet-zone indicator."),
    "guardrightpos": ("appearance", "Position of the right quiet-zone indicator."),
    "guardleftypos": ("appearance", "Vertical position of the left quiet-zone indicator."),
    "guardrightypos": ("appearance", "Vertical position of the right quiet-zone indicator."),
    "guarddescent": ("appearance", "How far the EAN/UPC guard bars extend below the normal bars."),
    "parse": ("encoder", "Interpret ^NNN escapes in the data as the character with that decimal code (^010 = newline, ^029 = GS)."),
    "parsefnc": ("encoder", "Interpret ^FNC1, ^FNC2 ... escapes in the data as function characters."),
    "includecheck": ("encoder", "Append the check digit(s) to the encoded data."),
    "includecheckintext": ("encoder", "Also show the check digit in the human readable text."),
    "validatecheck": ("encoder", "The data already contains the check digit; verify instead of calculating it."),
    "dontlint": ("encoder", "Skip the GS1 application identifier validation (lint) of the data."),
    "lintreqs": ("encoder", "Also enforce GS1 AI association requirements (which AIs must appear together)."),
    "gssep": ("encoder", "Use GS (0x1D) instead of FNC1 as the separator between variable-length AIs."),
    "eclevel": ("encoder", "Error correction level (QR: L, M, Q, H; PDF417: 0-8; Aztec: percent)."),
    "version": ("encoder", "Symbol version / size (QR: 1-40, Micro QR: M1-M4, Data Matrix: e.g. 10x10, 8x32 ...)."),
    "format": ("encoder", "Symbol format variant (Aztec: full, compact, rune; Data Matrix: square, rectangle; QR: full, micro)."),
    "mask": ("encoder", "Force a specific QR mask pattern (0-7), -1 = automatic."),
    "mode": ("encoder", "Encoding mode (MaxiCode: 2-6; Code One: A-H, S, T; Aztec: ...)."),
    "encoding": ("encoder", "Character encoding of the data (e.g. Code 39 extended, Telepen, Ultracode)."),
    "columns": ("encoder", "Number of data columns (PDF417, Data Matrix, Code 16K, Codablock F ...)."),
    "rows": ("encoder", "Number of rows (PDF417, Data Matrix, Codablock F ...)."),
    "layers": ("encoder", "Number of Aztec layers (symbol size)."),
    "ecaddchars": ("encoder", "Aztec: number of extra error correction codewords."),
    "c40headerlength": ("encoder", "Data Matrix: length of the C40 header for GS1/HIBC encodings."),
    "dmre": ("encoder", "Data Matrix: allow the rectangular extension sizes (DMRE)."),
    "raw": ("encoder", "The data is given as raw, pre-encoded codewords (^NNN syntax)."),
    "custinfoenc": ("encoder", "Australia Post: encoding of the customer information (character or numeric)."),
    "ccversion": ("encoder", "GS1 composite component version: a, b or c."),
    "cccolumns": ("encoder", "GS1 composite component: number of columns."),
    "segments": ("encoder", "GS1 DataBar Expanded Stacked: segments per row (even, 2-22)."),
    "linkage": ("encoder", "GS1 DataBar / GS1-128: set the linkage flag for a 2D composite."),
    "category": ("encoder", "D3 AQR: application category number (0-16383)."),
    "hostratio": ("encoder", "D3 AQR: host symbol ratio (2, 2.5 or 3)."),
    "sam": ("encoder", "MaxiCode: structured append position and total, e.g. 13 (symbol 1 of 3)."),
    "suppressc": ("encoder", "Code 128: never switch to code set C."),
    "unlatchextbeforec": ("encoder", "Code 128: unlatch the extended mode before switching to code set C."),
    "suppresskanjimode": ("encoder", "QR: never use Kanji mode."),
    "type": ("encoder", "Mailmark: symbol type (7, 9 or 29)."),
    "checktype": ("encoder", "Check digit algorithm variant."),
    "checkoffset": ("encoder", "Offset used by the check digit algorithm."),
    "ratio": ("encoder", "Wide-to-narrow ratio of the bars."),
    "link1": ("encoder", "Channel Code / composite link flag."),
}

_RENDERER_OPTIONS: dict[str, list[str]] = {
    "renlinear": [
        "includetext", "textfont", "textsize", "textgaps", "textxalign", "textyalign", "textxoffset", "textyoffset", "textcolor", "alttext",
        "height", "width", "inkspread", "inkspreadh", "inkspreadv", "showborder", "borderwidth", "borderleft", "borderright", "bordertop",
        "borderbottom", "bordercolor", "barcolor", "backgroundcolor", "guardwhitespace", "guardwidth", "guardheight", "guardleftpos",
        "guardrightpos", "guardleftypos", "guardrightypos",
    ],
    "renmatrix": [
        "includetext", "textfont", "textsize", "textxalign", "textyalign", "textxoffset", "textyoffset", "textcolor", "alttext",
        "width", "height", "inkspread", "inkspreadh", "inkspreadv", "showborder", "borderwidth", "borderleft", "borderright", "bordertop",
        "borderbottom", "bordercolor", "barcolor", "backgroundcolor", "dotty",
    ],
    "renmaximatrix": ["borderwidth", "borderleft", "borderright", "bordertop", "borderbottom", "bordercolor", "barcolor", "backgroundcolor"],
}
_RENDERER_DEFAULTS: dict[str, Any] = {
    "includetext": False, "textfont": "Helvetica", "textsize": 10, "textgaps": 0, "textxalign": "center", "textyalign": "below",
    "textxoffset": 0, "textyoffset": 0, "textcolor": "000000", "alttext": "", "height": 1, "width": 0, "inkspread": 0.15, "inkspreadh": 0,
    "inkspreadv": 0, "showborder": False, "borderwidth": 0.5, "borderleft": 10, "borderright": 10, "bordertop": 1, "borderbottom": 1,
    "bordercolor": "000000", "barcolor": "000000", "backgroundcolor": "FFFFFF", "guardwhitespace": False, "guardwidth": 6, "guardheight": 7,
    "guardleftpos": 0, "guardrightpos": 0, "guardleftypos": 0, "guardrightypos": 0, "dotty": False,
}
_CHOICES: dict[str, list[str]] = {
    "textxalign": ["left", "center", "right", "offleft", "offright", "justify"],
    "textyalign": ["below", "center", "above"],
    "ccversion": ["a", "b", "c"],
    "custinfoenc": ["character", "numeric"],
}
_COLOR_OPTIONS = {"barcolor", "backgroundcolor", "bordercolor", "textcolor", "extratextcolor", "color"}

# charset phrases from the validation messages -> regex character class
_CHARSET_PHRASES: list[tuple[str, str]] = [
    ("digits", "0-9"),
    ("capital letters", "A-Z"),
    ("uppercase letters", "A-Z"),
    ("letters", "A-Za-z"),
    ("spaces", " "),
    ("space", " "),
    ("dashes", "\\-"),
    ("dash", "\\-"),
]


@functools.lru_cache(maxsize=1)
def wiki_docs() -> dict[str, Any]:
    """Option descriptions extracted from the BWIPP wiki (tools/extract_option_docs.py)."""
    try:
        return json.loads(_DOCS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"options": {}, "encoders": {}}


def option_description(encoder: str, name: str) -> str:
    """Encoder-specific wiki note > curated short description > generic wiki description."""
    docs = wiki_docs()
    specific = docs.get("encoders", {}).get(encoder, {}).get(name)
    if specific:
        return specific
    if name in OPTION_DOCS:
        return OPTION_DOCS[name][1]
    return docs.get("options", {}).get(name, "")


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
def _blocks() -> dict[str, bytes]:
    """resource name -> decoded binary-token PostScript."""
    result: dict[str, bytes] = {}
    try:
        src = _bwipp_source()
    except Exception:
        return result
    for name, data in _BLOCK_RE.findall(src):
        try:
            result[name] = base64.a85decode(re.sub(r"\s", "", data).encode("ascii"), adobe=False)
        except Exception:
            continue
    return result


@functools.lru_cache(maxsize=1)
def headers() -> dict[str, dict[str, str]]:
    """encoder id -> {REQUIRES, DESC, EXAM, EXOP, RNDR, FMLY} from the ``% --BEGIN ENCODER`` headers."""
    result: dict[str, dict[str, str]] = {}
    try:
        src = _bwipp_source()
    except Exception:
        return result
    for m in _HEADER_RE.finditer(src):
        fields: dict[str, str] = {}
        for line in m.group(2).splitlines():
            lm = _HEADER_LINE_RE.match(line)
            if lm:
                fields[lm.group(1)] = lm.group(2).strip()
        result[m.group(1)] = fields
    return result


@functools.lru_cache(maxsize=1)
def bwipp_version() -> str:
    try:
        m = re.search(r"Barcode Writer in Pure PostScript - Version ([0-9-]+)", _bwipp_source()[:2000])
        return m.group(1) if m else "unknown"
    except Exception:
        return "unavailable"


def _rules_of_block(raw: bytes) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _RULE_RE.finditer(raw):
        code = m.group(1).decode("ascii")
        if m.group(2) is not None:
            n = m.group(2)[0]
        else:
            n = (m.group(3)[0] << 8) | m.group(3)[1]
        text = raw[m.end(): m.end() + n].decode("latin-1").strip()
        if not text or text in seen or len(text) < 4:
            continue
        seen.add(text)
        rules.append({"code": code, "message": text})
    return rules


@functools.lru_cache(maxsize=1)
def rules_by_encoder() -> dict[str, list[dict[str, str]]]:
    """encoder id -> [{code, message}] in source order, duplicates removed."""
    result: dict[str, list[dict[str, str]]] = {}
    for name, raw in _blocks().items():
        rules = _rules_of_block(raw)
        if rules:
            result[name] = rules
    return result


# helper blocks whose messages describe options or internals, not the data format
_HELPER_BLOCKS = {"preamble", "raiseerror", "setuphooks", "processoptions", "parseinput", "setanycolor", "fifocache", "rsecbinary", "rsecprime", "render", "renlinear", "renmatrix", "renmaximatrix"}


@functools.lru_cache(maxsize=None)
def rules_for(encoder: str) -> list[dict[str, str]]:
    """Data format rules of an encoder including those of the encoders it delegates to (gs1-128 -> gs1process)."""
    by_enc = rules_by_encoder()
    result = list(by_enc.get(encoder, []))
    seen = {r["message"] for r in result}
    for block in headers().get(encoder, {}).get("REQUIRES", "").split():
        if block in _HELPER_BLOCKS:
            continue
        for r in by_enc.get(block, []):
            if r["message"] not in seen:
                seen.add(r["message"])
                result.append(r)
    return result


def _defaults_of_block(raw: bytes) -> dict[str, Any]:
    """``/name value def`` pairs before the encoder calls processoptions."""
    end = raw.find(b"//processoptions")
    if end < 0:
        return {}
    out: dict[str, Any] = {}
    pos = 0
    while True:
        m = _DEFAULT_RE.search(raw, pos, end)
        if not m:
            break
        name = m.group(1).decode("ascii")
        e = m.end()
        value: Any
        if m.group(2):
            value = m.group(2) == b"true"
        elif m.group(3):
            value = struct.unpack(">b", m.group(3))[0]
        elif m.group(4):
            value = struct.unpack(">h", m.group(4))[0]
        elif m.group(5):
            value = struct.unpack(">i", m.group(5))[0]
        elif m.group(6):
            value = round(struct.unpack(">f", m.group(6))[0], 4)
        else:
            n = m.group(7)[0]
            value = raw[e: e + n].decode("latin-1")
            e += n
        if raw[e: e + 2] == _DEF_TOKEN:
            out[name] = value
        pos = e
    return out


@functools.lru_cache(maxsize=1)
def _defaults() -> dict[str, dict[str, Any]]:
    return {name: _defaults_of_block(raw) for name, raw in _blocks().items()}


def _option_type(name: str, default: Any) -> str:
    if name in _COLOR_OPTIONS:
        return "color"
    if name in _CHOICES:
        return "enum"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    return "string"


def _enum_from_rules(rules: list[dict[str, str]]) -> dict[str, list[str]]:
    """'hostratio must be 2, 2.5 or 3' -> {'hostratio': ['2', '2.5', '3']}"""
    out: dict[str, list[str]] = {}
    for r in rules:
        m = re.match(r"^(?:[A-Za-z0-9 -]+ )?(\w+) must be ((?:[A-Za-z0-9.]+, )*[A-Za-z0-9.]+,? or [A-Za-z0-9.]+)$", r["message"])
        if m:
            out[m.group(1)] = [v.strip() for v in re.split(r",\s*|\s+or\s+", m.group(2)) if v.strip()]
        m = re.match(r"^(\w+) must be one of ([A-Za-z0-9.* ]+)$", r["message"])
        if m:
            vals = m.group(2).replace(" or ", " ").split()
            out[m.group(1)] = vals
    return out


def _required_from_rules(rules: list[dict[str, str]]) -> set[str]:
    out: set[str] = set()
    for r in rules:
        m = re.match(r"^(?:The )?(\w+) option is required", r["message"])
        if m:
            out.add(m.group(1))
    return out


@functools.lru_cache(maxsize=None)
def options_of(encoder: str) -> list[dict[str, Any]]:
    """Every option the encoder accepts: its own, those of the encoders it delegates to, and its renderer's."""
    hdr = headers().get(encoder)
    if hdr is None:
        return []
    chain = [encoder] + hdr.get("REQUIRES", "").split()
    defaults = _defaults()
    merged: dict[str, Any] = {}
    own: set[str] = set()
    for block in reversed(chain):
        for k, v in defaults.get(block, {}).items():
            public = _ALIASES.get(k, k)
            merged[public] = v
            if block == encoder:
                own.add(public)
    rules_all: list[dict[str, str]] = []
    by_enc = rules_by_encoder()
    for block in chain:
        rules_all += by_enc.get(block, [])
    required = _required_from_rules(rules_all)
    enums = _enum_from_rules(rules_all)
    for token in hdr.get("EXOP", "").split():
        name, _, value = token.partition("=")
        if name and name not in merged and name not in _INTERNAL:
            merged[name] = (int(value) if value.isdigit() else value) if value else True
            own.add(name)
    renderer = hdr.get("RNDR", "")
    for name in _RENDERER_OPTIONS.get(renderer, []):
        if name not in merged:
            if name in ("includetext", "textfont", "textsize", "textgaps", "textxalign", "textyalign", "textxoffset", "textyoffset", "textcolor", "alttext") \
                    and renderer == "renmatrix" and not any(n in merged for n in ("includetext", "textsize")):
                continue  # matrix renderer without text support in this encoder
            merged[name] = _RENDERER_DEFAULTS.get(name)
    if "addongap" not in merged:
        for name in ("addontextfont", "addontextsize", "addontextxoffset", "addontextyoffset", "addondescent"):
            merged.pop(name, None)
    else:
        for name in ("addontextsize", "addontextxoffset", "addontextyoffset"):
            merged.setdefault(name, None)
    result: list[dict[str, Any]] = []
    for name, default in merged.items():
        if name in _INTERNAL:
            continue
        group = OPTION_DOCS.get(name, ("encoder" if name in own else "advanced", ""))[0]
        doc = option_description(encoder, name)
        if name in own and group in ("encoder", "advanced"):
            group = "encoder"
        typ = _option_type(name, default)
        choices = enums.get(name) or _CHOICES.get(name)
        if name == "eclevel" and encoder in ("qrcode", "microqrcode", "rectangularmicroqrcode", "gs1qrcode", "hibcqrcode", "d3aqr", "swissqrcode", "gs1dlqrcode"):
            choices = ["L", "M", "Q", "H"]
        if name == "format" and encoder in ("azteccode", "azteccodecompact", "aztecrune"):
            choices = ["full", "compact", "rune"]
        if name == "format" and "datamatrix" in encoder:
            choices = ["square", "rectangle"]
        if choices:
            typ = "enum"
        if default in ("unset", "", None) or (isinstance(default, (int, float)) and not isinstance(default, bool) and default < 0 and name not in ("inkspread", "textyoffset", "textxoffset")):
            default_out = None  # BWIPP uses -1 / "unset" as "automatic"
        else:
            default_out = default
        result.append(
            {
                "name": name,
                "type": typ,
                "default": default_out,
                "required": name in required,
                "choices": choices,
                "description": doc,
                "group": "required" if name in required else group,
            }
        )
    order = {"required": 0, "encoder": 1, "text": 2, "appearance": 3, "advanced": 4}
    result.sort(key=lambda o: (order.get(o["group"], 9), o["name"]))
    return result


def pattern_from_rules(rules: list[dict[str, str]]) -> str | None:
    """Regex (JavaScript/Python compatible) for the allowed characters, derived from 'must contain only ...' messages."""
    for r in rules:
        low = r["message"].lower()
        if "ais must start with '('" in low:
            return r"^(\(\d{2,4}\)[^()]*)+$"
        m = re.search(r"must contain only (.+)$", low)
        if not m:
            continue
        spec = m.group(1).strip().rstrip(".")
        if spec == "ascii characters":
            return r"^[\x00-\x7F]*$"
        if spec == "digits":
            return r"^[0-9]*$"
        if re.match(r"^digits 1 to 9$", spec):
            return r"^[1-9]*$"
        mm = re.match(r"^characters ([a-z](?:, [a-z])*(?:,? and [a-z])?)$", spec)
        if mm:
            chars = re.findall(r"(?<![a-z])([a-z])(?![a-z])", mm.group(1))
            return "^[" + "".join(c.upper() for c in chars) + "]*$"
        mm = re.match(r"^digits and letters ([a-z](?: [a-z])+)$", spec)
        if mm:
            return "^[0-9" + "".join(c.upper() for c in mm.group(1).split()) + "]*$"
        if spec == "digits and capital letters except o":
            return r"^[0-9A-NP-Z]*$"
        cls = ""
        rest = spec
        for phrase, chars in _CHARSET_PHRASES:
            if re.search(r"\b" + re.escape(phrase) + r"\b", rest):
                cls += chars
                rest = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", rest)
        sm = re.search(r"symbols? (.+)$", rest)
        if sm:
            for ch in sm.group(1).replace(" and ", " ").replace(",", " ").split():
                for c in ch:
                    cls += "\\" + c if c in "-]\\^" else c
        if cls:
            return "^[" + cls + "]*$"
    return None


_LEN_PATTERNS = [
    (re.compile(r"must be (\d+) or (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[2]))),
    (re.compile(r"must be (\d+) to (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[2]))),
    (re.compile(r"must be between (\d+) and (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[2]))),
    (re.compile(r"must be (\d+) (?:digits|characters)"), lambda m: (int(m[1]), int(m[1]))),
    (re.compile(r"must be at least (\d+)"), lambda m: (int(m[1]), None)),
    (re.compile(r"(?:must not exceed|at most|exceed) (\d+)"), lambda m: (None, int(m[1]))),
]


_GENERIC_SUBJECT = re.compile(r"^(the )?(data|input|message|barcode|symbol|text|length|data length)\b")


def summarize(rules: list[dict[str, str]], description: str = "") -> dict[str, Any]:
    """Best-effort structured hints derived from the rule messages.

    Length hints are only taken from rules that talk about the whole data (subject is the
    symbology name or a generic word), not about a part of it (add-on, DPID, postcode ...).
    The full rule list is always shown, so a missing summary never hides a rule.
    """
    charset = None
    length_hits: list[tuple[int | None, int | None]] = []
    desc = description.lower()
    for r in rules:
        low = r["message"].lower()
        if charset is None:
            if re.search(r"must contain only digits\s*$", low) or re.search(r"must be \d+( or \d+| to \d+)? digits", low):
                charset = "digits"
            elif re.search(r"only (capital letters and digits|uppercase letters and digits|digits and capital letters|alphanumeric)", low):
                charset = "alphanumeric"
            elif "must contain only" in low:
                charset = "restricted"
        for pat, fn in _LEN_PATTERNS:
            m = pat.search(low)
            if m:
                subject = low[: m.start()].strip()
                if (desc and subject == desc) or (desc and subject.startswith(desc)) or _GENERIC_SUBJECT.match(subject) or subject == "":
                    length_hits.append(fn(m))
                break
    min_len = max_len = None
    if len(length_hits) == 1:
        min_len, max_len = length_hits[0]
    elif len(length_hits) > 1:
        # several rules about the whole data: keep the widest range so valid input is never blocked
        lows = [lo for lo, _ in length_hits if lo is not None]
        highs = [hi for _, hi in length_hits if hi is not None]
        min_len = min(lows) if lows and len(lows) == len(length_hits) else None
        max_len = max(highs) if highs and len(highs) == len(length_hits) else None
    return {"charset": charset, "min_length": min_len, "max_length": max_len, "pattern": pattern_from_rules(rules)}


def wiki_url(description: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", description).strip("-")
    return f"https://github.com/bwipp/postscriptbarcode/wiki/{slug}"


def category_of(bcid: str) -> str:
    return _category_index().get(bcid.lower(), "Other")
