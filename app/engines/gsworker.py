"""Persistent Ghostscript workers for BWIPP.

treepoem starts Ghostscript twice per barcode (a bbox pass and a render pass) and each
start parses the 800 kB BWIPP PostScript library and loads fonts: roughly 1.5 s per
image. This module keeps Ghostscript processes alive with BWIPP already loaded and feeds
them one job at a time over stdin, which brings a barcode down to a few tens of
milliseconds.

Each job renders twice inside the warm process: a cheap 72 dpi pass on a large page to
measure the symbol (the page is pre-filled with a sentinel colour, everything that is not
sentinel belongs to the symbol), then the real pass at the requested resolution on a page
of exactly that size. Output is written as raw PPM into a private temp directory and read
with Pillow.

If anything goes wrong (Ghostscript missing, worker crashed, symbol larger than the
measuring page) the caller falls back to treepoem.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from binascii import hexlify
from dataclasses import dataclass

from PIL import Image

log = logging.getLogger(__name__)

SENTINEL = (254, 1, 253)          # page background used to find the symbol bounds
MEASURE_PAGE_PT = 1400            # measuring page (points at 72 dpi); symbols larger than this fall back
MARGIN_PT = 60                    # symbol origin offset on the measuring page (text may extend below the baseline)
MAX_RENDER_PIXELS = 40_000_000


class GsJobError(RuntimeError):
    """BWIPP reported an error for this job (bad data, unknown option ...)."""


class GsWorkerUnavailable(RuntimeError):
    """Worker could not be started or died; caller should fall back."""


def _bwipp_path() -> str:
    import treepoem

    return os.path.join(os.path.dirname(treepoem.__file__), "postscriptbarcode", "barcode.ps")


def _hex(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return "<" + hexlify(s).decode("ascii") + ">"


def _format_options(options: dict[str, str | bool]) -> str:
    parts = []
    for k, v in options.items():
        if isinstance(v, bool):
            if v:
                parts.append(k)
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


@dataclass
class _Job:
    bcid: str
    data: str | bytes
    options: str          # formatted BWIPP option string
    zoom: int             # PostScript scale factor (1 pt = 1 px at 72 dpi, so zoom = pixels per point)
    page_w: float         # page size in pixels
    page_h: float
    x: float              # symbol origin in points (unscaled coordinates)
    y: float
    out_file: str


class GsWorker:
    """One Ghostscript process with BWIPP loaded."""

    _counter = 0

    def __init__(self, timeout: float = 15.0):
        gs = shutil.which("gs")
        if not gs:
            raise GsWorkerUnavailable("ghostscript (gs) not installed")
        self.timeout = timeout
        self.dir = tempfile.mkdtemp(prefix="codeforge-gs-")
        GsWorker._counter += 1
        self.name = f"gs{GsWorker._counter}"
        self.jobs = 0
        self.lock = threading.Lock()
        self.proc = subprocess.Popen(
            [
                gs, "-q", "-dNOPAUSE", "-dSAFER", "-dNOPROMPT",
                f"--permit-file-write={self.dir}/",
                "-sDEVICE=ppmraw", "-r72", f"-sOutputFile={self.dir}/init.ppm",
                "-dGraphicsAlphaBits=1", "-dTextAlphaBits=1",
                _bwipp_path(), "-",
            ],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        # Ghostscript reads a non-tty stdin with fread(), which blocks until its buffer is full.
        # A background reader lets _exec wait with a timeout, and every job is padded with
        # whitespace so the interpreter never waits for "more input" in the middle of a job.
        self._lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._reader, name=f"{self.name}-reader", daemon=True).start()
        # warm up: fonts + one tiny symbol so the first real request is fast, and verify the protocol
        try:
            self._exec("/Helvetica findfont 10 scalefont setfont /Courier findfont 10 scalefont setfont")
            self._run(_Job("code128", "warmup", "includetext", 1, 300, 100, 10, 10, f"{self.dir}/warm.ppm"))
        except Exception as exc:  # pragma: no cover - environment dependent
            self.close()
            raise GsWorkerUnavailable(f"ghostscript worker failed to start: {exc}") from None
        log.info("%s: ghostscript worker ready (pid %s)", self.name, self.proc.pid)

    # --------------------------------------------------------------- protocol
    PADDING = " " * 16384 + "\n"

    def _reader(self) -> None:
        assert self.proc.stdout
        try:
            for line in iter(self.proc.stdout.readline, ""):
                self._lines.put(line)
        finally:
            self._lines.put(None)

    def _exec(self, ps: str) -> str:
        """Send PostScript, wait for the DONE marker, return everything printed before it."""
        if self.proc.poll() is not None:
            raise GsWorkerUnavailable("ghostscript worker exited")
        assert self.proc.stdin
        marker = "@@CF_DONE@@"
        try:
            self.proc.stdin.write(ps + f"\n(\\n{marker}\\n) print flush\n" + self.PADDING)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise GsWorkerUnavailable(f"ghostscript worker stdin closed: {exc}") from None
        lines: list[str] = []
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.kill()
                raise GsWorkerUnavailable("ghostscript worker timed out")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                raise GsWorkerUnavailable("ghostscript worker closed its output")
            line = line.rstrip("\n")
            if line.endswith(marker):
                head = line[: -len(marker)]
                if head:
                    lines.append(head)
                return "\n".join(lines)
            lines.append(line)

    def _run(self, job: _Job) -> str:
        r, g, b = (c / 255 for c in SENTINEL)
        ps = f"""
{{
  << /PageSize [{job.page_w} {job.page_h}] /OutputFile ({job.out_file}) >> setpagedevice
  gsave {r:.4f} {g:.4f} {b:.4f} setrgbcolor clippath fill grestore
  {job.zoom} {job.zoom} scale
  0 setgray /Helvetica findfont 10 scalefont setfont
  {job.x} {job.y} moveto
  {_hex(job.data)} {_hex(job.options)} {_hex(job.bcid)} cvn
  /uk.co.terryburton.bwipp findresource exec
  showpage
}} stopped {{
  (@@CF_ERR@@ ) print
  $error /errorname get dup length string cvs print ( ) print
  $error /errorinfo get dup type /stringtype eq {{ print }} {{ pop }} ifelse (\\n) print
  $error /newerror false put
  grestoreall initgraphics erasepage
}} if
"""
        out = self._exec(ps)
        self.jobs += 1
        for line in out.splitlines():
            if line.startswith("@@CF_ERR@@"):
                msg = line[len("@@CF_ERR@@"):].strip()
                if msg.startswith("bwipp."):
                    raise GsJobError("BWIPP ERROR: " + msg)
                raise GsJobError(msg)
        return out

    # ----------------------------------------------------------------- render
    def render(self, bcid: str, data: str | bytes, options: dict[str, str | bool], scale: int) -> Image.Image:
        opts = _format_options(options)
        measure = f"{self.dir}/m.ppm"
        final = f"{self.dir}/f.ppm"
        with self.lock:
            # pass 1: measure at 72 dpi (1 pt = 1 px)
            self._run(_Job(bcid, data, opts, 1, MEASURE_PAGE_PT, MEASURE_PAGE_PT, MARGIN_PT, MARGIN_PT, measure))
            with Image.open(measure) as im:
                bbox = _symbol_bbox(im)
                if bbox is None:
                    raise GsJobError("BWIPP produced an empty image")
                left, top, right, bottom = bbox
                if left == 0 or top == 0 or right >= im.width or bottom >= im.height:
                    raise GsWorkerUnavailable("symbol larger than the measuring page")
            # ppm rows are top-down, PostScript y is bottom-up
            x0_pt = left - MARGIN_PT
            y0_pt = (MEASURE_PAGE_PT - bottom) - MARGIN_PT
            w_pt, h_pt = right - left, bottom - top
            if (w_pt * scale) * (h_pt * scale) > MAX_RENDER_PIXELS:
                raise GsJobError("resulting image is too large, reduce scale")
            # pass 2: page of exactly bbox*scale pixels, drawing scaled by `scale`,
            # symbol moved so that its bbox starts at 0,0
            self._run(_Job(bcid, data, opts, scale, w_pt * scale, h_pt * scale, -x0_pt, -y0_pt, final))
            with Image.open(final) as im:
                img = im.convert("RGB")
        _replace_sentinel(img)
        return img

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                if self.proc.stdin:
                    try:
                        self.proc.stdin.write("quit\n")
                        self.proc.stdin.flush()
                    except Exception:
                        pass
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)

    def kill(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


def _symbol_bbox(im: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of every pixel that is not the sentinel colour."""
    from PIL import ImageChops

    return ImageChops.invert(_sentinel_mask(im)).getbbox()


def _sentinel_mask(im: Image.Image) -> Image.Image:
    """'L' mask: 255 where the pixel is exactly the sentinel colour."""
    from PIL import ImageChops

    r, g, b = im.convert("RGB").split()
    diff = ImageChops.add(
        ImageChops.add(ImageChops.difference(r, Image.new("L", im.size, SENTINEL[0])), ImageChops.difference(g, Image.new("L", im.size, SENTINEL[1]))),
        ImageChops.difference(b, Image.new("L", im.size, SENTINEL[2])),
    )
    return diff.point(lambda v: 255 if v == 0 else 0)


def _replace_sentinel(img: Image.Image) -> None:
    """Any sentinel pixel left inside the symbol bbox is unpainted background -> white."""
    mask = _sentinel_mask(img)
    if mask.getbbox() is not None:
        img.paste((255, 255, 255), mask=mask)


class GsPool:
    """A small pool of workers; threads borrow one for the duration of a render."""

    def __init__(self, size: int = 2, timeout: float = 15.0):
        self.size = max(1, size)
        self.timeout = timeout
        self._idle: queue.Queue[GsWorker] = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()
        self._broken = False

    def _acquire(self) -> GsWorker:
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self.size:
                self._created += 1
                try:
                    return GsWorker(self.timeout)
                except Exception:
                    self._created -= 1
                    raise
        return self._idle.get(timeout=self.timeout * 2)

    def render(self, bcid: str, data: str | bytes, options: dict[str, str | bool], scale: int) -> Image.Image:
        worker = self._acquire()
        try:
            return worker.render(bcid, data, options, scale)
        except GsWorkerUnavailable:
            worker.kill()
            with self._lock:
                self._created -= 1
            raise
        except GsJobError:
            raise
        except Exception:
            worker.kill()
            with self._lock:
                self._created -= 1
            raise
        finally:
            if worker.proc.poll() is None:
                self._idle.put(worker)

    def stats(self) -> dict:
        return {"size": self.size, "created": self._created, "idle": self._idle.qsize()}


_pool: GsPool | None = None
_pool_lock = threading.Lock()


def get_pool(size: int, timeout: float) -> GsPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = GsPool(size, timeout)
        return _pool
