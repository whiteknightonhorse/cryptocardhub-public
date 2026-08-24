"""FFmpeg render pipeline.

Three-pass composition (per polymarket vg V10):
  Pass 1: B-roll clip prep — videos use stream_loop+trim; photos use loop+zoompan
  Pass 2: Concat + overlays + word-level captions + 5 dynamic effects
  Pass 3: Mux voice + ducked music

Critical invariants (DO NOT BREAK):
  - duration = min(max_seconds, tts_seconds + 0.5)  (Y49 2026-08-11; replaces
    the 38 s floor of vg5 2026-05-05 — the floor padded short scripts with up
    to 41% voiceless b-roll, and retention died where the voice stopped).
    max_seconds defaults to the 60 s Shorts spec; the long-form pilot is the
    only caller that raises it (Y52)
  - mixed video/photo branch: skip zoompan on .mp4 clips (vg5 2026-05-05)
  - filter_complex concat with per-input aformat (NEVER -f concat demuxer; ytl #12)
  - amix duration=first (voice is master; music truncates)

For dry-run: produces a valid 1080x1920 H.264 yuv420p mp4 with
solid-color photos + silence audio mux. Verifies the full filter chain
without external API deps.
"""
from __future__ import annotations
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from . import _config


# --- Y54: CTA card geometry -------------------------------------------------
# 420 px = 39% of the 1080 frame. Measured, not guessed: encoding the page URL
# with UTM gives a version-5..7 QR (37x37..45x45 modules), and round-tripping it
# through cv2 off a frame downscaled to 400 px wide — a phone playing the Short —
# decodes at 420 px and fails at 320. Real capture through a camera is worse
# than that simulation, so the margin sits on the large side deliberately.
_QR_PX = 420
_CTA_MARGIN_PX = 40
_CTA_FONTSIZE_MAX = 88
_CTA_FONTSIZE_MIN = 34


def _is_video(path: str) -> bool:
    return path.lower().endswith((".mp4", ".mov", ".webm", ".mkv"))


def _fit_fontsize(text: str, font_path: str, max_width_px: int) -> int:
    """Largest size in [MIN, MAX] at which `text` fits `max_width_px`.

    Measured with PIL rather than estimated from a character-width ratio:
    Anton is condensed, and a ratio that is wrong by 10% either clips the URL
    or wastes half the card.
    """
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - PIL ships with qrcode
        return _CTA_FONTSIZE_MAX
    for size in range(_CTA_FONTSIZE_MAX, _CTA_FONTSIZE_MIN - 1, -2):
        if ImageFont.truetype(font_path, size).getlength(text) <= max_width_px:
            return size
    return _CTA_FONTSIZE_MIN


def _qr_png(payload: str, out: Path, px: int) -> Path:
    """QR for `payload`, `px` square, on an opaque white quiet zone.

    Error correction M (~15%): the picture behind it is a solid CTA card, not
    a textured frame, so the budget is better spent on fewer modules.
    """
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    img.resize((px, px)).save(out)
    return out


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", str(path)], timeout=20,
    )
    return float(json.loads(out)["format"]["duration"])


def _ass_path(words: list[dict[str, Any]], out_path: Path,
              fps: int = 30, font: str = "Anton-Regular",
              size: int = 80, y_pct: int = 75) -> Path:
    """Write a minimal ASS subtitle file with per-word events.

    Style: white fill, black border. Position: y_pct of frame height.
    Highlight tokens (digits, $-amounts, KEYWORDS) get yellow fill.
    """
    import re
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "Bold, Outline, Alignment, MarginL, MarginR, MarginV",
        f"Style: Def,{font},{size},&H00FFFFFF,&H00000000,1,6,2,40,40,{int(1920*(1-y_pct/100))}",
        f"Style: Hi,{font},{size},&H0004C2F7,&H00000000,1,6,2,40,40,{int(1920*(1-y_pct/100))}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    hi_re = re.compile(r"^(\d+%?|\$[\d.]+[MmKkBb]?|YES|NO|NEVER|ALWAYS|NEWS|"
                       r"BREAKING|SHOCK|CRASH|SPIKE|BOOM|NEW|FIRST|LAST|ONLY|"
                       r"EVERY|NOBODY|EVERYONE|FREE|EARN|YIELD|CASHBACK|TAP)$",
                       re.IGNORECASE)
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        style = "Hi" if hi_re.match(text.strip(",.!?:;\"'")) else "Def"
        s = _ass_time(w["start"])
        e = _ass_time(w["end"])
        # ASS escape: replace backslashes and braces
        text_safe = text.replace("\\", " ").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{s},{e},{style},,0,0,0,,{text_safe}")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - 60 * (60 * h + m)
    return f"{h:d}:{m:02d}:{s:05.2f}"


def render(*, voiceover_path: Path, music_path: Path,
           clips: list[dict[str, Any]], words: list[dict[str, Any]],
           ftc_overlay_path: Path | None,
           target_seconds: float, out_path: Path,
           platform: str = "youtube",
           cta_url: str = "defycard.com",
           qr_payload: str | None = None,
           cta_seconds: float = 6.0,
           font_path: str | None = None,
           max_seconds: float = 60.0,
           burn_captions: bool = True) -> Path:
    """Render the final mp4. Returns out_path.

    Args:
      clips: list of {kind, path, source, ...}. `kind` is 'video' or 'photo'.
      cta_url: text shown in the end-of-video CTA overlay (default 'defycard.com').
               Human-readable, no UTM — it is read off a screen, not clicked.
      qr_payload: URL encoded into the QR on the CTA card, or None for no QR.
               A PARAMETER, not an env read: a renderer that decides its own
               output from ambient state and leans on one caller to compensate
               is exactly what left longform and IG/TT captionless (Y52).
      cta_seconds: how long the CTA stays on screen at the END (default 6s — per
                   user feedback 2026-05-06: '2x longer than current')
      font_path: explicit font for CTA overlay; default = bundled Anton-Regular.
      max_seconds: hard ceiling on the output. 60 s is the Shorts output spec
                   and stays the default for every caller; only the long-form
                   pilot (Y52, pipeline/longform.py) raises it.
      burn_captions: burn the ASS caption track into the picture. Default True —
                   a renderer that silently drops its own captions is not doing
                   its job. Only a caller that adds captions back by other means
                   passes False (main.py does, for the DEFY_NEW_DESIGN HTML
                   overlay). Was an ambient env check read here directly, which
                   left longform and IG/TT captionless (Y52).
    """
    cfg = _config.cfg()
    W = cfg["output"]["resolution"]["width"]
    H = cfg["output"]["resolution"]["height"]
    fps = cfg["output"]["fps"]
    pix_fmt = cfg["output"]["video"]["pixel_format"]
    crf = cfg["output"]["video"]["crf"]
    preset = cfg["output"]["video"]["preset"]

    # Voice-video duration sync (Y49 2026-08-11). Speech sets the length:
    # 0.5 s of tail, 60 s hard cap (output spec). `target_seconds` is advisory
    # only — it may not stretch the video past the voiceover any more.
    tts_dur = _ffprobe_duration(voiceover_path)
    duration = min(max_seconds, tts_dur + 0.5)

    n = len(clips)
    if n == 0:
        raise RuntimeError("No clips to render")
    per_clip = duration / n

    # Build filter_complex
    inputs: list[str] = []  # ffmpeg -i args
    fc_parts: list[str] = []  # filter graph segments
    concat_labels: list[str] = []

    for i, c in enumerate(clips):
        path = c["path"]
        if _is_video(path):
            inputs += ["-stream_loop", "-1", "-t", f"{per_clip:.3f}", "-i", path]
            # videos: scale + crop + fps + setsar (NO zoompan — vg5 invariant)
            fc_parts.append(
                f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},fps={fps},setsar=1,format={pix_fmt},"
                f"trim=duration={per_clip:.3f},setpts=PTS-STARTPTS[v{i}]"
            )
        else:
            inputs += ["-loop", "1", "-t", f"{per_clip:.3f}", "-i", path]
            # photos: zoompan Ken Burns (gentle, 1.0 → 1.06 over the clip)
            zoom_frames = int(per_clip * fps)
            fc_parts.append(
                f"[{i}:v]scale=1620:2880:force_original_aspect_ratio=increase,"
                f"zoompan=z='min(zoom+0.0006,1.06)':d={zoom_frames}:s={W}x{H}:fps={fps},"
                f"format={pix_fmt},setsar=1,trim=duration={per_clip:.3f},"
                f"setpts=PTS-STARTPTS[v{i}]"
            )
        concat_labels.append(f"[v{i}]")

    fc_parts.append("".join(concat_labels) + f"concat=n={n}:v=1:a=0[bg]")

    # FTC overlay — burn in for 5–8 seconds
    next_label = "bg"
    if ftc_overlay_path and ftc_overlay_path.exists():
        ftc_idx = len(clips)
        inputs += ["-loop", "1", "-t", "8", "-i", str(ftc_overlay_path)]
        fc_parts.append(f"[{ftc_idx}:v]format=rgba[ftc]")
        fc_parts.append(
            f"[bg][ftc]overlay=x=0:y=H-h-40:enable='between(t,0,8)':format=auto[fg]"
        )
        next_label = "fg"

    # Captions via ASS subtitle file
    ass_path = out_path.with_suffix(".ass")
    _ass_path(words, ass_path)
    if burn_captions:
        fc_parts.append(f"[{next_label}]ass='{ass_path}'[afterCC]")
    else:
        fc_parts.append(f"[{next_label}]null[afterCC]")  # caller re-adds them (HTML overlay)

    # End-of-video CTA overlay — persistent text "<cta_url>" for the LAST
    # `cta_seconds` of the video. User feedback 2026-05-06: previous render
    # had no explicit end CTA → URL flashed for ~0.5s in caption stream and
    # was unreadable. This drawtext overlay runs for cta_seconds (default 6)
    # over a translucent black box, lower-third position.
    cta_font = font_path or str(_config.root() / "assets" / "fonts" / "Anton-Regular.ttf")
    cta_start = max(0.0, duration - cta_seconds)
    # Escape colon and special chars in font path per FFmpeg drawtext syntax.
    cta_font_esc = cta_font.replace(":", r"\:")
    cta_text_esc = cta_url.replace(":", r"\:").replace("'", r"\'")
    # Y54: the text is the page's own URL now, so its length varies with the
    # slug. Shrink to fit rather than fall back to the bare domain — showing
    # "defycard.com" under a video about India is the money bug Y50/Y53 closed.
    cta_fontsize = _fit_fontsize(cta_url, cta_font, W - 2 * _CTA_MARGIN_PX)
    cta_filter = (
        f"drawtext=fontfile='{cta_font_esc}'"
        f":text='{cta_text_esc}'"
        f":fontsize={cta_fontsize}:fontcolor=white"
        f":borderw=6:bordercolor=black"
        f":x=(w-text_w)/2:y=h-h*0.28"
        f":box=1:boxcolor=black@0.55:boxborderw=24"
        f":enable='between(t,{cta_start:.3f},{duration:.3f})'"
    )
    qr_label = "final" if not qr_payload else "withCTA"
    fc_parts.append(f"[afterCC]{cta_filter}[{qr_label}]")

    # Audio: voice + music ducked. amix duration=first (voice is master).
    # NOTE: removed the legacy `adelay=300|300` on voice — caption sync (now
    # millisecond-accurate via edge-tts WordBoundary sidecar) requires the
    # voice mp3 to start at video t=0, otherwise captions LEAD by 300ms.
    # Music's afade=in:0:0.6 still gives a soft entry without needing the gap.
    voice_idx = len(clips) + (1 if ftc_overlay_path and ftc_overlay_path.exists() else 0)
    inputs += ["-i", str(voiceover_path)]
    music_idx = voice_idx + 1
    inputs += ["-i", str(music_path)]
    fc_parts.append(
        f"[{voice_idx}:a]aformat=channel_layouts=stereo,volume=1.3[aV]"
    )
    fc_parts.append(
        f"[{music_idx}:a]aformat=channel_layouts=stereo,afade=t=in:st=0:d=0.6,"
        f"afade=t=out:st={duration-1.0:.3f}:d=1.0,volume=0.22[aM]"
    )
    fc_parts.append("[aV][aM]amix=inputs=2:duration=first:normalize=0[a]")

    # Y54: QR on the CTA card — the only machine-passable route out of a Short,
    # where the description URL is a text node with no navigationEndpoint (Y52).
    # It carries the SAME destination as the description and the pinned comment
    # (the caller passes both from one `topic_path`), but as a different string:
    # full https + UTM, because a scanner needs the scheme and GA4 needs the
    # markup, while the human reading the text below it needs neither.
    #
    # Appended AFTER the audio inputs on purpose — voice_idx/music_idx above are
    # computed from the input count, so inserting a video input earlier would
    # silently remap the audio.
    if qr_payload:
        qr_idx = music_idx + 1
        qr_png = out_path.parent / "_cta_qr.png"
        _qr_png(qr_payload, qr_png, _QR_PX)
        inputs += ["-loop", "1", "-t", f"{cta_seconds:.3f}", "-i", str(qr_png)]
        # Upper-middle: clear of the Shorts right-hand button column and of the
        # bottom strip that carries the title/description chrome.
        fc_parts.append(
            f"[{qr_idx}:v]format=rgba,setpts=PTS-STARTPTS+{cta_start:.3f}/TB[qr];"
            f"[withCTA][qr]overlay=x=(W-w)/2:y=H*0.30:"
            f"enable='between(t,{cta_start:.3f},{duration:.3f})'[final]"
        )

    fc = ";".join(fc_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[final]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", pix_fmt, "-preset", preset, "-crf", str(crf),
        "-r", str(fps), "-g", str(fps * 2),
        "-c:a", "aac", "-b:a", cfg["output"]["audio"]["bitrate"],
        "-ar", str(cfg["output"]["audio"]["sample_rate"]),
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1500:]}")
    return out_path
