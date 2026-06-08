#!/usr/bin/env python3
"""
handycam-to-mkv — back up unfinalized (or finalized) Sony Handycam mini-DVDs
on Linux, with no transcoding.

Flow per disc:
  1. Make sure the tray starts open.
  2. Wait for you to insert a disc + close the tray  (press 'q' while waiting to quit).
  3. Ask for a title -> that becomes the output filename.
  4. ddrescue the raw disc to an image (single -n pass, no retries).
  5. Carve the MPEG-2 program stream out of the image (find the first 00 00 01 BA pack header).
  6. Remux through ffmpeg into an .mkv (stream copy, no transcode) to fix timestamps/duration.
  7. Drop the .mkv into ./titles, delete temp files, eject, and loop.

Needs read access to the optical device (run as root or be in the 'cdrom'/'disk' group),
plus ddrescue and ffmpeg on PATH. Linux only (uses the CDROM ioctls).
"""

import argparse
import datetime
import fcntl
import glob
import mmap
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty

# ---- Linux CDROM ioctls (from <linux/cdrom.h>) -----------------------------
CDROMEJECT = 0x5309          # open the tray / eject media
CDROM_DRIVE_STATUS = 0x5326  # query drive status

CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4

_STATUS_NAMES = {
    CDS_NO_INFO: "no info",
    CDS_NO_DISC: "no disc",
    CDS_TRAY_OPEN: "tray open",
    CDS_DRIVE_NOT_READY: "drive not ready",
    CDS_DISC_OK: "disc ready",
}

# ---- tiny console helpers --------------------------------------------------
_USE_COLOR = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def info(msg):
    print(_c("36", "•") + " " + msg)


def good(msg):
    print(_c("32", "✓") + " " + msg)


def warn(msg):
    print(_c("33", "!") + " " + msg)


def err(msg):
    print(_c("31", "✗") + " " + msg)


def banner():
    line = "─" * 52
    print(_c("36", line))
    print(_c("1;36", "  Handycam mini-DVD archiver"))
    print(_c("36", line))


# ---- device control --------------------------------------------------------
def drive_status(device):
    """Return one of the CDS_* constants, or CDS_NO_INFO on error."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return CDS_NO_INFO
    try:
        return fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0)
    except OSError:
        return CDS_NO_INFO
    finally:
        os.close(fd)


def _try_ioctl_eject(device):
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, CDROMEJECT)
            return True
        finally:
            os.close(fd)
    except OSError:
        return False


def open_tray(device):
    """
    Open the tray and verify it actually opened. Escalates from the kernel
    ioctl to `eject` (which also unmounts any desktop auto-mount) to a forced
    `eject -F`, retrying until the drive reports CDS_TRAY_OPEN. Returns True
    once the tray is open, False if every method failed.
    """
    have_eject = bool(shutil.which("eject"))
    attempts = (
        ("ioctl", lambda: _try_ioctl_eject(device)),
        ("eject", lambda: have_eject and subprocess.run(
            ["eject", device], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL).returncode == 0),
        ("force eject", lambda: have_eject and subprocess.run(
            ["eject", "-F", device], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL).returncode == 0),
    )
    for _, action in attempts:
        action()
        # give the mechanism a moment to actually move the tray
        for _ in range(6):
            if drive_status(device) == CDS_TRAY_OPEN:
                return True
            time.sleep(0.5)
    return drive_status(device) == CDS_TRAY_OPEN


def ensure_tray_open(device):
    if drive_status(device) == CDS_TRAY_OPEN:
        return
    info("Opening tray...")
    if not open_tray(device):
        warn("Couldn't open the tray automatically — open it manually if needed.")


def read_key():
    """Read a single keypress without waiting for Enter. Falls back to a line
    read when stdin isn't a tty (e.g. piped input)."""
    if not sys.stdin.isatty():
        return (sys.stdin.readline().strip()[:1] or "")
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


# ---- waiting for a disc (with 'q' to quit) ---------------------------------
def wait_for_disc_or_quit(device):
    """
    Block until a readable disc is present (returns 'disc') or the user
    presses q (returns 'quit'). Spinner + non-blocking key read via cbreak.
    """
    print()
    info("Insert a disc and close the tray.  (press " + _c("1", "q") + " to quit)")
    spinner = "|/-\\"
    frame = 0
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        while True:
            status = drive_status(device)
            if status == CDS_DISC_OK:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
                return "disc"

            label = _STATUS_NAMES.get(status, "unknown")
            sys.stdout.write(f"\r\033[K  {spinner[frame % len(spinner)]} waiting… ({label}) ")
            sys.stdout.flush()
            frame += 1

            r, _, _ = select.select([sys.stdin], [], [], 0.25)
            if r:
                ch = sys.stdin.read(1)
                if ch.lower() == "q":
                    sys.stdout.write("\r\033[K")
                    sys.stdout.flush()
                    return "quit"
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


# ---- the actual work -------------------------------------------------------
def ask_title(default=None):
    # If we detected a recording date, offer it as the default: Enter accepts it,
    # 'c' switches to typing a custom title.
    if default:
        prompt = _c("1", f"Title for this disc [{default}]") + _c(
            "2", "  (Enter to accept, 'c' for custom): ")
        choice = input(prompt).strip()
        if choice.lower() != "c":
            safe = sanitize_filename(choice) if choice else sanitize_filename(default)
            if safe:
                return safe
            warn("That title has no usable characters — enter one manually.")
    while True:
        raw = input(_c("1", "Title for this disc: ")).strip()
        if not raw:
            warn("Title can't be empty.")
            continue
        safe = sanitize_filename(raw)
        if not safe:
            warn("That title has no usable characters — try again.")
            continue
        if safe != raw:
            info(f"Using filename: {safe}")
        return safe


def sanitize_filename(name):
    name = name.replace("/", "-").replace("\\", "-")
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "", name)
    name = name.strip(" .")
    return name[:200]


# ---- disc detection (type + recording date) --------------------------------
# Unfinalized Sony mini-DVDs are DVD-VR: per-recording timestamps live in
# VR_MANGR.IFO (magic DVD_RTR_VMG0), which we parse directly (the `dvd-vr`
# tool's format) rather than scan blindly. Finalized discs are plain DVD-Video
# (VIDEO_TS.IFO magic DVDVIDEO-VMG) and get imaged to a faithful .iso instead.
_VR_IFO_MAGIC = b"DVD_RTR_VMG0"
_DVDVIDEO_MAGIC = b"DVDVIDEO-VMG"


def _decode_pgtm(b):
    """Decode a 5-byte bit-packed DVD-VR timestamp into a datetime, or None."""
    year = ((b[0] << 8) | b[1]) >> 2
    month = ((b[1] & 0x03) << 2) | (b[2] >> 6)
    day = (b[2] & 0x3E) >> 1
    hour = ((b[2] & 0x01) << 4) | (b[3] >> 4)
    minute = ((b[3] & 0x0F) << 2) | (b[4] >> 6)
    sec = b[4] & 0x3F
    if not year or not (1995 <= year <= datetime.date.today().year + 1):
        return None
    try:
        return datetime.datetime(year, month, day, hour, minute, sec)
    except ValueError:
        return None


def _parse_vr_ifo(buf):
    """Given bytes containing VR_MANGR.IFO, return sorted list of recording
    datetimes (may be empty)."""
    base = buf.find(_VR_IFO_MAGIC)
    if base < 0:
        return []
    ifo = buf[base:]

    def be32(o):
        return struct.unpack_from(">I", ifo, o)[0]

    def be16(o):
        return struct.unpack_from(">H", ifo, o)[0]

    try:
        pgit_sa = be32(256)                     # byte offset to program-info table
        nr_vob_formats = ifo[pgit_sa + 3]
        # vob_format_t is a PACKED 60-byte struct (no padding — the crucial detail)
        pgi_gi = pgit_sa + 8 + nr_vob_formats * 60
        nr_programs = be16(pgi_gi)
        sa_arr = pgi_gi + 2                      # array of u32 offsets (from pgiti)
        stamps = []
        for i in range(nr_programs):
            vvob = pgit_sa + be32(sa_arr + 4 * i)   # vvob_t; pgtm at +2 (5 bytes)
            dt = _decode_pgtm(ifo[vvob + 2:vvob + 7])
            if dt:
                stamps.append(dt)
        return sorted(stamps)
    except (struct.error, IndexError):
        return []


def _read_device(device, cap=48 * 1024 * 1024):
    """Sequentially read up to `cap` bytes from `device`, stopping early once we
    can identify the disc: the DVD-Video magic ends the probe immediately, while
    the DVD-VR IFO magic reads 64 KB further to capture its program table.
    Sequential only — seeking is unreliable on optical drives. Returns bytes."""
    chunks, total, vr_at = [], 0, -1
    try:
        fd = os.open(device, os.O_RDONLY)
    except OSError:
        return b""
    try:
        while total < cap:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if vr_at < 0:
                buf = b"".join(chunks)
                if _DVDVIDEO_MAGIC in buf:
                    break
                vr_at = buf.find(_VR_IFO_MAGIC)
            elif total - vr_at >= 65536:
                break
    finally:
        os.close(fd)
    return b"".join(chunks)


def _udf_label_stamp(device):
    """Read the Sony UDF volume label and return it as a title. Sony writes a
    full timestamp there (e.g. '2009_03_14_12H54M_AM'); use it verbatim when it
    matches, else the bare date, else None. The label is already filename-safe."""
    if not shutil.which("udfinfo"):
        return None
    try:
        out = subprocess.run(["udfinfo", device], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^label=(\d{4}_\d{2}_\d{2}_\d{2}H\d{2}M_(?:AM|PM))",
                  out, re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"^label=(\d{4}_\d{2}_\d{2})", out, re.MULTILINE)
    if m:
        return m.group(1)
    return None


def detect_disc(device):
    """Identify the disc and a suggested title. Returns (kind, title) where
    kind is 'dvd-vr', 'dvd-video', or 'unknown', and title may be None.
    DVD-VR recording dates come from the IFO program table; DVD-Video uses the
    volume-label timestamp."""
    buf = _read_device(device)
    stamps = _parse_vr_ifo(buf)
    if stamps:
        oldest, newest = stamps[0].date(), stamps[-1].date()
        if oldest == newest:
            title = oldest.strftime("%Y_%m_%d")
        else:
            title = f"{oldest.strftime('%Y_%m_%d')}-{newest.strftime('%Y_%m_%d')}"
        return "dvd-vr", title
    if _DVDVIDEO_MAGIC in buf:
        return "dvd-video", _udf_label_stamp(device)
    return "unknown", _udf_label_stamp(device)


# ---- live single-line progress (ddrescue / ffmpeg) -------------------------
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PCT_RE = re.compile(r"pct rescued:\s*([\d.]+%)")
_ETA_RE = re.compile(r"remaining time:\s*([^\n,]+)")
_RATE_RE = re.compile(r"current rate:\s*([^\n,]+)")
_PHASE_RE = re.compile(r"(Copying|Trimming|Scraping|Retrying|Finished)[^\n]*")
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def _status_line(text):
    """
    Overwrite the current terminal line, leaving one space after the text so
    the cursor doesn't sit flush against it (no-op when output isn't a tty).
    """
    if sys.stdout.isatty():
        sys.stdout.write("\r\033[K" + text + " ")
        sys.stdout.flush()


def _end_status(wrote):
    if wrote and sys.stdout.isatty():
        sys.stdout.write("\n")
        sys.stdout.flush()


def _hms_to_s(h, m, s):
    return int(h) * 3600 + int(m) * 60 + float(s)


def ddrescue_disc(device, img_path, map_path):
    if not shutil.which("ddrescue"):
        err("ddrescue not found on PATH.")
        return False
    # single fast pass, block size 2048 (DVD sector), no scraping/retries
    cmd = ["ddrescue", "-b", "2048", "-n", device, img_path, map_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=0)
    buf = ""
    last = ""
    spin = "|/-\\"
    frame = 0
    try:
        while True:
            chunk = proc.stdout.read(128)
            if not chunk:
                break
            buf = (buf + chunk.decode("utf-8", "replace"))[-4096:]
            clean = _ANSI_RE.sub("", buf)
            phase = _PHASE_RE.findall(clean)
            frame += 1
            if phase and phase[-1] not in ("Copying", "Finished"):
                # Trimming/scraping/retry passes run backwards with no ETA and
                # near-zero rate, so the normal line looks frozen — animate.
                line = f"  reading… {spin[frame % len(spin)]} checking for unreadable areas…"
            else:
                pct = _PCT_RE.findall(clean)
                eta = _ETA_RE.findall(clean)
                rate = _RATE_RE.findall(clean)
                parts = []
                if pct:
                    parts.append(pct[-1])
                if eta:
                    e = eta[-1].strip()
                    parts.append(f"~{e} left" if e != "n/a" else "estimating…")
                if rate:
                    parts.append(rate[-1].strip())
                if not parts:
                    continue
                line = "  reading… " + "  ".join(parts)
            if line != last:
                _status_line(line)
                last = line
    finally:
        proc.stdout.close()
    _end_status(bool(last))
    return proc.wait() == 0


def carve_mpeg(img_path, out_path):
    """Find the first MPEG-2 PS pack header and copy from there to out_path."""
    if os.path.getsize(img_path) == 0:
        err("Disc image is empty — nothing was read from the disc.")
        return False
    with open(img_path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            offset = mm.find(b"\x00\x00\x01\xba")
        finally:
            mm.close()
    if offset < 0:
        err("No MPEG-2 pack header (00 00 01 BA) found — disc isn't MPEG video, or read failed.")
        return False
    info(f"MPEG-2 stream starts at byte {offset:,}")
    with open(img_path, "rb") as src, open(out_path, "wb") as dst:
        src.seek(offset)
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    return True


def remux_to_mkv(mpg_path, mkv_path):
    if not shutil.which("ffmpeg"):
        err("ffmpeg not found on PATH.")
        return False
    # stream copy only — fixes timestamps/duration, no re-encode
    cmd = ["ffmpeg", "-y", "-i", mpg_path, "-c", "copy", mkv_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, bufsize=0)
    buf = ""
    total = None
    spin = "|/-\\"
    frame = 0
    wrote = False
    try:
        while True:
            chunk = proc.stderr.read(128)
            if not chunk:
                break
            buf = (buf + chunk.decode("utf-8", "replace"))[-4096:]
            if total is None:
                m = _DUR_RE.search(buf)
                if m:
                    total = _hms_to_s(*m.groups())
            tmatches = _TIME_RE.findall(buf)
            if tmatches:
                cur = _hms_to_s(*tmatches[-1])
                if total and total > 0:
                    pct = max(0.0, min(100.0, cur / total * 100))
                    _status_line(f"  remuxing… {pct:4.1f}%")
                else:
                    _status_line(f"  remuxing… {spin[frame % 4]} {cur:.0f}s")
                    frame += 1
                wrote = True
    finally:
        proc.stderr.close()
    rc = proc.wait()
    if wrote:
        _status_line("  remuxing… done")
    _end_status(wrote)
    return rc == 0


_KIND_LABELS = {
    "dvd-vr": "DVD-VR recording",
    "dvd-video": "finalized DVD-Video",
    "unknown": "unrecognized disc",
}


def process_disc(device, exports_dir):
    kind, suggested = detect_disc(device)
    info(f"Detected: {_KIND_LABELS[kind]}"
         + (f" ({suggested})" if suggested else ""))
    title = ask_title(suggested)
    os.makedirs(exports_dir, exist_ok=True)
    if kind == "dvd-video":
        # A finalized DVD can be kept whole (ISO) or split into per-chapter MKVs.
        # Single keypress — no Enter required.
        sys.stdout.write(_c("1", "Output?") + _c(
            "2", "  [i] ISO   [c] split chapters (default i): "))
        sys.stdout.flush()
        choice = read_key().lower()
        print(choice if choice.isalnum() else "")
        if choice == "c":
            archive_chapters(device, exports_dir, title)
        else:
            archive_iso(device, exports_dir, title)
    else:
        archive_mkv(device, exports_dir, title)


def archive_iso(device, exports_dir, title):
    """Finalized DVD-Video: the ddrescue image is already a valid ISO, so write
    it straight into the output dir (avoids a multi-GB temp copy)."""
    out_iso = os.path.join(exports_dir, title + ".iso")
    if os.path.exists(out_iso):
        warn(f"{out_iso} already exists — it will be overwritten.")
    tmpdir = tempfile.mkdtemp(prefix="dvdrip_")
    mapfile = os.path.join(tmpdir, "disc.mapfile")
    try:
        print()
        info("Imaging DVD to ISO (ddrescue)")
        if not ddrescue_disc(device, out_iso, mapfile):
            err("ddrescue failed. Skipping this disc.")
            if os.path.exists(out_iso):
                os.remove(out_iso)
            return
        size_mb = os.path.getsize(out_iso) / (1024 * 1024)
        print()
        good(f"Done: {os.path.basename(out_iso)}  ({size_mb:.1f} MB)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _chapter_dates(image, n_chapters):
    """Best-effort per-chapter recording date. Finalized DVD-Video usually has
    NO embedded date (verified: the IFO loses it and the stream carries no
    user_data), so this returns [None]*n there and the caller falls back to
    index names. Only when the title VOB actually contains many MPEG user_data
    blocks do we attempt to decode dates from them — conservatively, accepting
    only sane datetimes. UNVERIFIED against a date-bearing disc by design.
    """
    none = [None] * n_chapters
    dates = []
    blocks = 0
    tail = b""
    try:
        with open(image, "rb") as f:
            # bounded streaming scan — user_data is rare/absent on these discs
            scanned = 0
            while scanned < 2 * 1024 * 1024 * 1024:        # cap at 2 GB
                chunk = f.read(8 * 1024 * 1024)
                if not chunk:
                    break
                scanned += len(chunk)
                buf = tail + chunk
                i = 0
                while True:
                    j = buf.find(b"\x00\x00\x01\xb2", i)   # user_data_start_code
                    if j < 0 or j + 16 > len(buf):
                        break
                    blocks += 1
                    dt = _decode_pgtm(buf[j + 4:j + 9])    # try DVD-VR pgtm form
                    if dt:
                        dates.append(dt)
                    i = j + 4
                tail = buf[-3:]
    except OSError:
        return none
    # Negligible user_data ⇒ no per-chapter date available ⇒ index names.
    if blocks < n_chapters or len(dates) < n_chapters:
        return none
    uniq = sorted(set(dates))
    if len(uniq) < n_chapters:
        return none
    return [d.strftime("%Y_%m_%d_%HH%MM") for d in uniq[:n_chapters]]


def archive_chapters(device, exports_dir, folder_title):
    """Finalized DVD-Video, alternative mode: split each chapter into its own
    MKV inside a folder named after the disc. Rescue to a temp image, remux the
    main title (chapters preserved) and split it with mkvmerge."""
    if not shutil.which("mkvmerge"):
        err("mkvmerge not found on PATH — install mkvtoolnix. Skipping.")
        return
    out_dir = os.path.join(exports_dir, folder_title)
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        warn(f"{out_dir}/ already exists and is not empty — files may be overwritten.")
    os.makedirs(out_dir, exist_ok=True)

    tmpdir = tempfile.mkdtemp(prefix="dvdrip_", dir=exports_dir)
    iso = os.path.join(tmpdir, "disc.iso")
    mapfile = os.path.join(tmpdir, "disc.mapfile")
    title_mkv = os.path.join(tmpdir, "title.mkv")
    part_base = os.path.join(tmpdir, "part.mkv")
    try:
        print()
        info("Step 1/3 — reading disc with ddrescue")
        if not ddrescue_disc(device, iso, mapfile):
            err("ddrescue failed. Skipping this disc.")
            return

        print()
        info("Step 2/3 — extracting title (ffmpeg)")
        rc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "dvdvideo", "-title", "0", "-i", iso,
             "-map", "0:v", "-map", "0:a", "-c", "copy", title_mkv],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc != 0 or not os.path.exists(title_mkv):
            err("ffmpeg title extraction failed. Skipping this disc.")
            return

        print()
        info("Step 3/3 — splitting into chapters (mkvmerge)")
        rc = subprocess.run(
            ["mkvmerge", "-q", "-o", part_base, "--split", "chapters:all", title_mkv],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        parts = sorted(glob.glob(os.path.join(tmpdir, "part-*.mkv")))
        if rc not in (0, 1) or not parts:           # mkvmerge rc 1 = warnings
            err("mkvmerge split failed. Skipping this disc.")
            return

        names = _chapter_dates(iso, len(parts))
        width = max(2, len(str(len(parts))))
        for idx, part in enumerate(parts):
            label = names[idx] if idx < len(names) and names[idx] else None
            name = label or f"{idx + 1:0{width}d}"
            shutil.move(part, os.path.join(out_dir, name + ".mkv"))

        print()
        good(f"Done: {folder_title}/  ({len(parts)} chapters)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def archive_mkv(device, exports_dir, title):
    """Unfinalized DVD-VR (or unrecognized): rescue the disc, carve the MPEG-2
    program stream, and remux to MKV (no transcode)."""
    out_mkv = os.path.join(exports_dir, title + ".mkv")
    if os.path.exists(out_mkv):
        warn(f"{out_mkv} already exists — it will be overwritten.")

    tmpdir = tempfile.mkdtemp(prefix="dvdrip_")
    img = os.path.join(tmpdir, "disc.img")
    mapfile = os.path.join(tmpdir, "disc.mapfile")
    mpg = os.path.join(tmpdir, "salvaged.mpg")
    try:
        print()
        info("Step 1/3 — reading disc with ddrescue")
        if not ddrescue_disc(device, img, mapfile):
            err("ddrescue failed. Skipping this disc.")
            return

        print()
        info("Step 2/3 — carving MPEG-2 stream")
        if not carve_mpeg(img, mpg):
            return

        print()
        info("Step 3/3 — remuxing to MKV")
        if not remux_to_mkv(mpg, out_mkv):
            err("ffmpeg remux failed. Skipping this disc.")
            return

        size_mb = os.path.getsize(out_mkv) / (1024 * 1024)
        print()
        good(f"Done: {os.path.basename(out_mkv)}  ({size_mb:.1f} MB)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---- main loop -------------------------------------------------------------
def preflight(device):
    missing = [t for t in ("ddrescue", "ffmpeg", "mkvmerge") if not shutil.which(t)]
    if missing:
        warn("Missing tools on PATH: " + ", ".join(missing) + " (see requirements.txt)")
    if not os.path.exists(device):
        err(f"Device {device} does not exist. Pass --device if your drive is elsewhere.")
        return False
    if not os.access(device, os.R_OK):
        warn(f"No read access to {device} — run as root or join the 'cdrom'/'disk' group.")
    return True


def main():
    ap = argparse.ArgumentParser(description="Archive Handycam mini-DVDs to MKV (no transcode).")
    ap.add_argument("--device", default="/dev/sr0", help="optical device (default: /dev/sr0)")
    ap.add_argument("--exports", default="titles", help="output folder (default: ./titles)")
    args = ap.parse_args()

    exports_dir = os.path.abspath(args.exports)

    banner()
    if not preflight(args.device):
        sys.exit(1)
    info(f"Device:  {args.device}")
    info(f"Titles:  {exports_dir}")

    try:
        first = True
        while True:
            # On the first run, use a disc that's already in the drive instead
            # of ejecting it; afterwards, eject and wait for the next one.
            if first and drive_status(args.device) == CDS_DISC_OK:
                good("Disc already inserted.")
            else:
                ensure_tray_open(args.device)
                if wait_for_disc_or_quit(args.device) == "quit":
                    print()
                    info("Exiting.")
                    break
                good("Disc detected.")
            first = False
            process_disc(args.device, exports_dir)
            print()
            info("Ejecting...")
            if not open_tray(args.device):
                warn("Couldn't eject the tray — remove the disc manually.")
    except KeyboardInterrupt:
        print()
        info("Interrupted.")


if __name__ == "__main__":
    main()
