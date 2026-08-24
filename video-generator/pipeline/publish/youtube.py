"""YouTube Data API v3 publisher.

Critical invariants per polymarket youtube-longform:
  - Credentials() WITHOUT scopes= for refresh-token reuse (#19)
  - tags[] 500-char wire incl. quote overhead (#21)
  - NO https:// or www. in description for unverified channel (#4)
  - thumbnails.set requires SMS verification at youtube.com/verify
  - Y45: every YT API call routes through a per-channel NordVPN SOCKS5
    proxy so YT doesn't see 8 channels on this host co-publishing from
    one Hetzner IP.

Phase 1 default: privacy='unlisted' for first 5 TG-preview-approved videos.
Phase 2+ flips to 'public'.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Any, Optional


# Y52 — the format of the file and the markup of its description must agree.
# YouTube classifies by duration alone: <=180 s vertical is a Short (its
# description link is a plain text node, not clickable), >180 s is a normal
# watch page. Uploading a long-form file with `utm_medium=shorts` makes its
# sessions indistinguishable from Shorts; uploading a Short with
# `utm_medium=longform` invents a click path that does not exist. Both are
# caught here, before the network.
_SHORTS_MAX_SECONDS = 180.0
_LONGFORM_MIN_SECONDS = 185.0


def _probe_seconds(path: Path) -> float | None:
    import json as _json
    import subprocess as _sp
    try:
        out = _sp.check_output(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
             str(path)], timeout=30,
        )
        return float(_json.loads(out)["format"]["duration"])
    except Exception:
        return None


def validate_format_markup(video_path: Path, description: str) -> float | None:
    """Raise before any API call when duration and campaign markup disagree.

    Returns the measured duration (None when it could not be measured — an
    unmeasurable file is not blocked here; the pipeline's own gate covers it).
    """
    dur = _probe_seconds(video_path)
    if dur is None:
        return None
    if dur <= _SHORTS_MAX_SECONDS:
        if "utm_medium=shorts" not in description:
            raise ValueError(
                f"{video_path.name} is {dur:.1f}s (a Short) but its description "
                f"does not carry utm_medium=shorts — refusing to upload"
            )
    elif dur >= _LONGFORM_MIN_SECONDS:
        if "utm_medium=longform" not in description:
            raise ValueError(
                f"{video_path.name} is {dur:.1f}s (long-form) but its "
                f"description does not carry utm_medium=longform — refusing "
                f"to upload"
            )
    else:
        raise ValueError(
            f"{video_path.name} is {dur:.1f}s — inside the "
            f"({_SHORTS_MAX_SECONDS}, {_LONGFORM_MIN_SECONDS})s dead zone at "
            f"the Shorts boundary, where the format is not determined; "
            f"refusing to upload"
        )
    return dur


def _credentials():
    """Build google.oauth2.Credentials WITHOUT scopes= (ytl #19)."""
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )


def _yt(channel_id: Optional[str] = None):
    """Build a youtube/v3 client.

    When `channel_id` is provided AND NordVPN service credentials are in
    .env, all API requests route through that channel's SOCKS5 exit
    (Y45). Falls back to direct Hetzner IP when proxy is unavailable
    (creds missing, all pool servers down, etc.) — the publish still
    succeeds, just without the IP-isolation signal.
    """
    from googleapiclient.discovery import build

    # Import lazily — the vpn_proxy module imports `yaml` and the
    # publish path must not crash if PyYAML isn't installed yet.
    proxy = None
    try:
        # Resolve sibling package import — `pipeline.vpn_proxy` from
        # `pipeline.publish.youtube`.
        from .. import vpn_proxy
        if not channel_id:
            channel_id = os.environ.get("YOUTUBE_CHANNEL_ID")
        proxy = vpn_proxy.get_proxy_for_channel(channel_id) if channel_id else None
        if proxy is not None:
            http = vpn_proxy.make_proxied_http(proxy, timeout=300)
            # google-auth expects an httplib2.Http with our token attached;
            # we use AuthorizedHttp wrapper.
            from google_auth_httplib2 import AuthorizedHttp
            authed = AuthorizedHttp(_credentials(), http=http)
            print(f"[publish/youtube] routing via {proxy} (channel={channel_id})",
                  file=sys.stderr)
            return build("youtube", "v3", http=authed, cache_discovery=False)
    except Exception as e:
        # Non-fatal — log and fall through to direct path.
        print(f"[publish/youtube] WARN: proxy setup failed, falling back "
              f"to direct: {type(e).__name__}: {e}", file=sys.stderr)

    return build("youtube", "v3", credentials=_credentials(), cache_discovery=False)


def refuse_placeholder_build(video_path: Path) -> None:
    """Refuse to upload a render built from dry-run placeholders.

    Y58 second line of defence. The first line lives in fetch_visuals (a live
    fetch no longer creates placeholders at all), but a gate that reads the
    value at the OUTPUT boundary catches producers that do not exist yet —
    which is the whole reason `Xm2-vLgwrc8` reached YouTube as four solid-blue
    slides: the defect was upstream, and nothing between it and the network
    looked at what was actually being shipped.
    """
    d = Path(video_path).parent
    stray = sorted((d / "media").glob("placeholder_*")) if (d / "media").is_dir() else []
    if stray:
        raise ValueError(
            f"{Path(video_path).name}: {len(stray)} placeholder asset(s) in "
            f"{d.name}/media (e.g. {stray[0].name}) — refusing to publish a "
            f"video of solid-colour slides"
        )
    summary = d / "summary.json"
    if summary.is_file():
        try:
            import json
            blob = json.loads(summary.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            blob = None
        if blob is not None and "placeholder" in json.dumps(blob):
            raise ValueError(
                f"{Path(video_path).name}: summary.json records a placeholder "
                f"source — refusing to publish"
            )


def upload(*, video_path: Path, title: str, description: str,
           tags: list[str], category_id: int = 28,
           privacy: str = "public",  # was 'unlisted' during operator-approval phase; flipped to public 2026-05-06 after first test video approved
           thumbnail_path: Path | None = None,
           pinned_comment: str | None = None,
           captions_srt_path: Path | None = None,
           recording_location: str = "United States",
           dry_run: bool = False) -> dict[str, Any]:
    """Upload `video_path` to the configured channel. Returns {video_id, url}.

    On dry_run / missing creds: returns {"dry_run": True, ...} without calling YouTube.
    """
    # Runs before the dry-run return on purpose: the validator must be
    # exercised by every dry run, not only by live publishes.
    validate_format_markup(Path(video_path), description)
    refuse_placeholder_build(Path(video_path))

    have_creds = all(os.environ.get(k) for k in (
        "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
    ))
    if dry_run or not have_creds:
        return {
            "dry_run": True, "video_path": str(video_path),
            "title": title, "tag_count": len(tags),
            "thumbnail_attached": bool(thumbnail_path),
            "pinned_attached": bool(pinned_comment),
            "captions_attached": bool(captions_srt_path and captions_srt_path.exists()),
        }

    from datetime import datetime, timezone
    from googleapiclient.http import MediaFileUpload, MediaInMemoryUpload

    yt = _yt()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": str(category_id),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            # NO `containsSyntheticMedia` flag. YouTube's synthetic-media
            # disclosure is for content that could mislead a viewer about a
            # REAL person/place/event (deepfakes, voice clones, fabricated
            # newsworthy events). Generic TTS narration of factual content
            # is explicitly EXCLUDED per YT's own examples (production
            # assistance, generic AI voice not impersonating anyone).
            # Setting it True triggers a visible "Altered or synthetic
            # content" warning label on the video → CTR penalty + viewer
            # distrust. Documented as Y40 in youtube1/SKILL.md (corrected
            # 2026-05-08 after misreading the policy).
        },
        # Recording metadata — geo + time signals to the algorithm. Y42.
        # We use upload time (close enough to recording for a same-day
        # render). locationDescription is editorial-team location, not
        # a per-video shoot location (that'd be misleading for VIRAL stock).
        "recordingDetails": {
            "recordingDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "locationDescription": recording_location,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True,
                            mimetype="video/mp4")
    req = yt.videos().insert(
        part="snippet,status,recordingDetails", body=body, media_body=media,
    )
    response = None
    while response is None:
        status, response = req.next_chunk()
    video_id = response["id"]
    out: dict[str, Any] = {
        "video_id": video_id, "url": f"https://youtu.be/{video_id}",
    }

    if thumbnail_path and thumbnail_path.exists():
        try:
            yt.thumbnails().set(videoId=video_id,
                                media_body=MediaFileUpload(str(thumbnail_path),
                                                           mimetype="image/jpeg")).execute()
            out["thumbnail_set"] = True
        except Exception as e:  # 403 if channel not SMS-verified
            out["thumbnail_error"] = str(e)[:200]

    if pinned_comment:
        try:
            cr = yt.commentThreads().insert(part="snippet", body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {"snippet": {"textOriginal": pinned_comment}},
                },
            }).execute()
            out["pinned_comment_id"] = cr.get("id")
        except Exception as e:
            out["pinned_comment_error"] = str(e)[:200]

    # English caption track via captions.insert (Y43). Raw SRT generated
    # upstream from voice.timestamps.json (WordBoundary alignment per Y23).
    # YT then auto-translates to other languages — free SEO for ES/PT/DE.
    if captions_srt_path and captions_srt_path.exists():
        try:
            srt_text = captions_srt_path.read_text(encoding="utf-8")
            cap = yt.captions().insert(
                part="snippet",
                body={"snippet": {
                    "videoId": video_id,
                    "language": "en",
                    "name": "English",
                    "isDraft": False,
                }},
                media_body=MediaInMemoryUpload(
                    srt_text.encode("utf-8"), mimetype="text/plain",
                ),
                sync=False,
            ).execute()
            out["caption_track_id"] = cap.get("id")
        except Exception as e:
            out["caption_track_error"] = str(e)[:200]

    return out
