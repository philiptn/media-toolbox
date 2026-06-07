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
import fcntl
import mmap
import os
import re
import select
import shutil
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
def ask_title():
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


def process_disc(device, exports_dir):
    title = ask_title()
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
        os.makedirs(exports_dir, exist_ok=True)
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
    missing = [t for t in ("ddrescue", "ffmpeg") if not shutil.which(t)]
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
        while True:
            ensure_tray_open(args.device)
            if wait_for_disc_or_quit(args.device) == "quit":
                print()
                info("Exiting.")
                break
            good("Disc detected.")
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
