"""Pydantic request / response models for the v1 API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Output = Literal["image", "base64", "json"]


class QrParams(BaseModel):
    data: str = Field(..., description="Text or URL to encode.", examples=["https://example.com"])
    error: str = Field("M", description="Error correction: L (7%), M (15%), Q (25%), H (30%).", examples=["M"])
    version: int | None = Field(None, ge=1, le=40, description="Symbol version 1-40, omit for automatic.")
    scale: int = Field(10, ge=1, description="Pixels per module.")
    border: int = Field(4, ge=0, description="Quiet zone in modules.")
    fg: str = Field("#000000", description="Foreground colour (#RRGGBB or CSS name).")
    bg: str = Field("#ffffff", description="Background colour (#RRGGBB or CSS name).")
    format: str = Field("png", description="png, svg, jpg, webp, gif, bmp or pdf.")
    micro: bool = Field(False, description="Generate a Micro QR code (versions M1-M4).")


class BarcodeParams(BaseModel):
    bcid: str = Field(..., description="Symbology id (BWIPP encoder name), e.g. code128, ean13, datamatrix, auspost.", examples=["code128"])
    text: str = Field(..., description="Data to encode, format depends on the symbology.", examples=["Count01234567!"])
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="BWIPP encoder options (includetext, textsize, eclevel, custinfoenc, barcolor ...) "
        "and renderer options (scale, scaleX, scaleY, rotate=N|R|L|I, paddingwidth, paddingheight, monochrome).",
        examples=[{"includetext": True, "scale": 3}],
    )
    format: str = Field("png", description="png, jpg, webp, gif, bmp or pdf (SVG is QR only).")


class BatchItem(BaseModel):
    type: Literal["qr", "barcode"] = "qr"
    caption: str | None = Field(None, description="Caption printed under the code on PDF sheets; defaults to the encoded text.")
    # qr fields
    data: str | None = None
    error: str = "M"
    version: int | None = None
    scale: int | None = None
    border: int | None = None
    fg: str = "#000000"
    bg: str = "#ffffff"
    micro: bool = False
    # barcode fields
    bcid: str | None = None
    text: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class SheetOptions(BaseModel):
    page: str = Field("A4", description="A4, LETTER, A4-LANDSCAPE, LETTER-LANDSCAPE")
    columns: int = Field(3, ge=1, le=20)
    rows: int = Field(8, ge=1, le=40)
    margin_mm: float = Field(10, ge=0, le=50)
    gap_mm: float = Field(4, ge=0, le=50)
    captions: bool = True
    title: str | None = None


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(..., min_length=1)
    output: Literal["json", "zip", "pdf"] = Field("json", description="json = base64 per item, zip = one file per item, pdf = label sheet")
    format: str = Field("png", description="Image format for json/zip output (png, svg for QR, jpg ...).")
    sheet: SheetOptions = Field(default_factory=SheetOptions)


class RenderedItem(BaseModel):
    index: int
    ok: bool
    mime: str | None = None
    format: str | None = None
    width: int | None = None
    height: int | None = None
    base64: str | None = None
    data_uri: str | None = None
    error: str | None = None


class BatchResponse(BaseModel):
    count: int
    succeeded: int
    failed: int
    items: list[RenderedItem]


class DecodedSymbol(BaseModel):
    type: str
    data: str
    quality: int | None = None
    rect: dict[str, int]
    polygon: list[dict[str, int]]


class DecodeResponse(BaseModel):
    count: int
    symbols: list[DecodedSymbol]


class DecodeBase64Request(BaseModel):
    image: str = Field(..., description="Base64 encoded image, optionally as data URI.")


class Symbology(BaseModel):
    id: str
    description: str
    example: str = ""
    example_options: str = ""


class Health(BaseModel):
    status: str
    version: str
    ghostscript: bool
    zbar: bool
    libdmtx: bool
    symbologies: int
    rate_limit: str
