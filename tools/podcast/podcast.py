# /// script
# requires-python = ">=3.11"
# dependencies = ["yt-dlp>=2026.1.1", "pyyaml>=6"]
# ///
"""podcast: transcripts and summaries for podcast episodes, usable by humans and agents.

Subcommands (all print JSON to stdout unless noted; logs go to stderr):
  shows                         list registered shows
  episodes SHOW [--limit N]     list recent episodes (index, number, date, title)
  fetch SHOW EPISODE            download captions, write transcript + metadata; cached per episode
  transcript SHOW EPISODE       same as fetch, but prints the transcript markdown to stdout
  summarize SHOW EPISODE        fetch, then write the summary with claude CLI or the OpenAI API

EPISODE can be: latest | latest-N | an episode number (304, #304, ep304) | a YouTube URL |
free text matched against episode titles ("chamath", "mercury into gold").

Summary engines: --engine claude uses the `claude` CLI (Claude Code login, no key needed);
--engine openai uses OPENAI_API_KEY (+ OPENAI_MODEL or --model); --print-prompt emits the prompt for any model.
Default engine: claude if the CLI is installed, else openai if OPENAI_API_KEY is set.

Exit codes: 0 ok, 1 error, 2 no captions available, 3 ambiguous episode text (candidates in JSON).
"""
from __future__ import annotations

import argparse
import os
import difflib
import email.utils
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import yaml
import yt_dlp

TOOL_DIR = Path(__file__).resolve().parent          # <project>/tools/podcast
REGISTRY = TOOL_DIR / "podcasts.yaml"
TEMPLATE = TOOL_DIR / "SUMMARY_TEMPLATE.md"
PROJECT_ROOT = TOOL_DIR.parent.parent
TRANSCRIPTS_SUBDIR = "transcripts"                  # summaries sit at the top of output/<show>/, the rest here
NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
PARAGRAPH_WORDS = 70  # target words per paragraph in the cleaned transcript


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"error: {msg}")
    sys.exit(code)


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-")


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def episode_number(title: str, pattern: str | None) -> int | None:
    m = re.search(pattern or r"#\s*(\d+)", title or "")
    return int(m.group(1)) if m else None


def norm_title(t: str) -> str:
    t = re.sub(r"\|?\s*(ep|episode)?\s*#\s*\d+", " ", t, flags=re.I)
    return re.sub(r"[^a-z0-9 ]+", " ", t.lower()).strip()


# ---------- RSS ----------

def load_feed(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "podcast-digest/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        root = ET.fromstring(r.read())
    items = []
    for it in root.find("channel").findall("item"):
        enc = it.find("enclosure")
        pub = it.findtext("pubDate")
        try:
            date = email.utils.parsedate_to_datetime(pub).astimezone(timezone.utc).date().isoformat()
        except Exception:
            date = None
        dur = it.findtext("itunes:duration", namespaces=NS)
        try:
            dur = int(dur) if dur and dur.isdigit() else sum(
                int(x) * 60 ** i for i, x in enumerate(reversed(dur.split(":")))) if dur else None
        except Exception:
            dur = None
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "date": date,
            "duration_sec": dur,
            "audio_url": enc.get("url") if enc is not None else None,
            "description": (it.findtext("description") or "").strip(),
            "guid": it.findtext("guid"),
        })
    return items


def pick_item(items: list[dict], spec: str, pattern: str | None, recent: bool = False) -> dict:
    raw = spec.strip()
    spec = raw.lower()
    if spec.startswith("latest"):
        off = int(spec[len("latest-"):]) if spec.startswith("latest-") else 0
        if off >= len(items):
            die(f"feed only has {len(items)} items; cannot take latest-{off}")
        return items[off]
    m = re.fullmatch(r"(?:ep(?:isode)?\s*)?#?\s*(\d+)", spec)
    if m:
        want = int(m.group(1))
        for it in items:
            if episode_number(it["title"], pattern) == want:
                return it
        die(f"no feed item with episode number {want} (feed has {len(items)} items)")
    # free text: score every title by token overlap and sequence similarity
    q = norm_title(raw)
    qtok = set(q.split())
    scored = []
    for it in items:
        t = norm_title(it["title"])
        ttok = set(t.split())
        overlap = len(qtok & ttok) / max(1, len(qtok))
        seq = difflib.SequenceMatcher(None, q, t).ratio()
        sub = 1.0 if q and q in t else 0.0
        scored.append((max(sub, 0.7 * overlap + 0.3 * seq), it))
    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    if best_score < 0.4:
        die(f"no episode title matches '{raw}'; try `episodes` to browse titles")
    runners = [it for sc, it in scored[1:4] if sc >= best_score - 0.1 and sc >= 0.4]
    if runners and recent:
        best = max([best] + runners, key=lambda x: x["date"] or "")
        runners = []
    if runners:
        cands = [{"episode_number": episode_number(x["title"], pattern), "date": x["date"], "title": x["title"]}
                 for x in [best] + runners]
        print(json.dumps({"error": "ambiguous", "query": raw, "candidates": cands,
                          "hint": "retry with the episode number, or add --recent to take the newest candidate"},
                         indent=2, ensure_ascii=False))
        sys.exit(3)
    log(f"matched '{raw}' -> {best['title']} (score {best_score:.2f})")
    return best


# ---------- YouTube ----------

def list_channel_videos(channel_url: str, limit: int) -> list[dict]:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlist_end": limit}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    return [{"id": e["id"], "title": e.get("title", ""), "duration": e.get("duration"),
             "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"}
            for e in info.get("entries", []) if e and e.get("id")]


def match_video(item: dict, videos: list[dict], pattern: str | None) -> dict:
    num = episode_number(item["title"], pattern)
    if num is not None:
        hits = [v for v in videos if episode_number(v["title"], pattern) == num]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            log(f"warning: {len(hits)} videos carry #{num}; picking the closest title")
            videos = hits
    target = norm_title(item["title"])
    scored = sorted(((difflib.SequenceMatcher(None, target, norm_title(v["title"])).ratio(), v)
                     for v in videos), key=lambda x: -x[0])
    if not scored:
        die("no videos found on the channel")
    ratio, best = scored[0]
    if ratio < 0.5:
        die(f"could not match '{item['title']}' to a channel video (best ratio {ratio:.2f}: '{best['title']}'). "
            f"Pass the YouTube URL as EPISODE, or raise --max-videos.")
    if ratio < 0.75:
        log(f"warning: fuzzy title match (ratio {ratio:.2f}): '{best['title']}'")
    return best


def video_info(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_captions(url: str, workdir: Path, langs: list[str]) -> tuple[Path, str, str]:
    """Return (vtt path, language, 'manual'|'auto')."""
    info = video_info(url)
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    chosen = None
    for lang in langs:
        if lang in manual:
            chosen, source = lang, "manual"
            break
    if chosen is None:
        for lang in langs:
            if lang in auto:
                chosen, source = lang, "auto"
                break
    if chosen is None:
        die(f"no captions in {langs} for {url}. Manual: {list(manual)[:5]} Auto: {list(auto)[:5]}. "
            f"Consider transcribing the audio with Whisper instead.", code=2)
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
        "writesubtitles": source == "manual", "writeautomaticsub": source == "auto",
        "subtitleslangs": [chosen], "subtitlesformat": "vtt",
        "outtmpl": str(workdir / "captions.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    vtts = list(workdir.glob("captions*.vtt"))
    if not vtts:
        die("yt-dlp reported success but no .vtt file was written")
    return vtts[0], chosen, source


# ---------- VTT cleaning ----------

TS_RE = re.compile(r"(\d+):(\d\d):(\d\d)\.(\d{3})")
TAG_RE = re.compile(r"<[^>]+>")
NOISE_RE = re.compile(r"\[(music|applause|laughter|inaudible)\]", re.I)


def parse_vtt(path: Path) -> list[tuple[float, str]]:
    """Return [(start_seconds, text)] with YouTube's rolling duplicate lines removed."""
    lines: list[tuple[float, str]] = []
    last = ""
    start = 0.0
    for block in path.read_text(encoding="utf-8", errors="replace").split("\n\n"):
        block = block.strip()
        if not block or block.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        rows = block.split("\n")
        if "-->" not in rows[0] and len(rows) > 1 and "-->" in rows[1]:
            rows = rows[1:]  # numbered cue
        m = TS_RE.search(rows[0]) if "-->" in rows[0] else None
        if not m:
            continue
        start = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        for row in rows[1:]:
            text = TAG_RE.sub("", row).replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
            text = re.sub(r"\s+", " ", text).strip()
            if not text or text == last:
                continue
            lines.append((start, text))
            last = text
    return lines


def build_transcript(lines: list[tuple[float, str]], chapters: list[dict]) -> str:
    out: list[str] = []
    chapters = sorted(chapters or [], key=lambda c: c["start_time"])
    ci = 0
    para: list[str] = []
    para_start = 0.0
    words = 0

    def flush():
        nonlocal para, words
        if para:
            out.append(f"[{fmt_ts(para_start)}] " + " ".join(para))
            out.append("")
        para, words = [], 0

    for start, raw in lines:
        while ci < len(chapters) and start >= chapters[ci]["start_time"]:
            flush()
            out.append(f"## {chapters[ci]['title']} [{fmt_ts(chapters[ci]['start_time'])}]")
            out.append("")
            ci += 1
        raw = NOISE_RE.sub(" ", raw)
        # ">>" marks a speaker change in YouTube captions: start a new paragraph there.
        for i, text in enumerate(x.strip() for x in raw.split(">>")):
            if i > 0:
                flush()
            if not text:
                continue
            if not para:
                para_start = start
            para.append(text)
            words += len(text.split())
            if words >= PARAGRAPH_WORDS and re.search(r"[.!?]$", text):
                flush()
            elif words >= PARAGRAPH_WORDS * 1.5:
                flush()
    flush()
    return "\n".join(out).rstrip() + "\n"


# ---------- commands ----------

SUMMARY_PROMPT = """You are summarizing a podcast episode from its transcript (YouTube auto-captions: no speaker
names, paragraph breaks mark speaker changes, names may be misspelled; cross-check names against the
episode description). Write the summary in Markdown following this template EXACTLY (same headings,
same order; the chapter list below gives the chapter sections):

{template}

Plain prose, no hype, paraphrase rather than quote, keep numbers and names. Output only the Markdown.

EPISODE METADATA (JSON):
{meta}

CHAPTERS:
{chapters}

TRANSCRIPT:
{transcript}
"""


def load_show(registry: dict, slug: str | None) -> dict | None:
    if not slug:
        return None
    show = registry.get(slug)
    if not show:
        die(f"unknown show '{slug}'; known: {', '.join(registry) or 'none'} (edit {REGISTRY})")
    return show


def detect_show(registry: dict, info: dict) -> tuple[str | None, dict | None]:
    """Match a video's channel against the registry's youtube URLs (by @handle or channel id)."""
    cands = {str(info.get(k) or "").lower() for k in ("uploader_url", "channel_url", "uploader_id", "channel_id", "channel")}
    cands.discard("")
    for slug, show in registry.items():
        yt = str(show.get("youtube", "")).lower()
        m = re.search(r"(@[\w.-]+)|/channel/([\w-]+)", yt)
        keys = [k for k in (m.group(1), m.group(2)) if k] if m else []
        if any(any(k in c for c in cands) for k in keys):
            return slug, show
    return None, None


def do_fetch(args, registry) -> dict:
    """Resolve the episode, return its metadata; downloads only when not cached."""
    url_mode = args.episode.startswith(("http://", "https://"))
    show_slug = args.show if (args.show and args.show != "-") else None
    show = load_show(registry, show_slug)
    if not url_mode and show is None:
        die("give a show slug (see `shows`) or a YouTube URL with '-' as the show")
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]

    # 1. identify the episode: RSS item (cheap) or YouTube info (URL mode)
    item = None
    info = None
    if url_mode:
        info = video_info(args.episode)
        url = args.episode
        if show is None:  # auto-detect a registered show from the video's channel
            show_slug, show = detect_show(registry, info)
            log(f"show: {show_slug or 'unknown channel, using channel name'}")
        if show is not None:  # fill metadata from the feed so URL fetches match numbered fetches
            pattern = show.get("episode_regex")
            num = episode_number(info.get("title", ""), pattern)
            feed_items = load_feed(show["feed"])
            if num is not None:
                item = next((it for it in feed_items if episode_number(it["title"], pattern) == num), None)
            if item is None:
                scored = max(((difflib.SequenceMatcher(None, norm_title(info.get("title", "")), norm_title(it["title"])).ratio(), it)
                              for it in feed_items), key=lambda x: x[0], default=(0, None))
                item = scored[1] if scored[0] >= 0.6 else None
            log(f"feed item: {item['title']} ({item['date']})" if item else "feed item: none matched; using YouTube metadata")
    else:
        pattern = show.get("episode_regex")
        log(f"fetching feed {show['feed']}")
        item = pick_item(load_feed(show["feed"]), args.episode, pattern, args.recent)
        log(f"feed item: {item['title']} ({item['date']})")
        url = None
    pattern = (show or {}).get("episode_regex")

    def ytdate(i):
        d = (i or {}).get("upload_date")
        return f"{d[:4]}-{d[4:6]}-{d[6:]}" if d else None

    slug = show_slug or slugify((info or {}).get("channel") or (info or {}).get("uploader") or "youtube", 30)
    title = item["title"] if item else info.get("title", "")
    date = (item or {}).get("date") or ytdate(info) or datetime.now().date().isoformat()
    num = episode_number(title, pattern) or episode_number((info or {}).get("title", ""), pattern)
    stem = f"{slug}-ep{num}-{date}" if num is not None else f"{slug}-{date}-{slugify(title)}"
    out_dir = Path(args.out) if args.out else PROJECT_ROOT / "output" / slug
    (out_dir / TRANSCRIPTS_SUBDIR).mkdir(parents=True, exist_ok=True)
    files = {"episode": f"{TRANSCRIPTS_SUBDIR}/{stem}-episode.json",
             "captions": f"{TRANSCRIPTS_SUBDIR}/{stem}-captions.vtt",
             "transcript": f"{TRANSCRIPTS_SUBDIR}/{stem}-transcript.md",
             "summary": f"{stem}-summary.md"}

    # 2. cache hit: no YouTube calls at all
    if not args.force and (out_dir / files["episode"]).exists() and (out_dir / files["transcript"]).exists():
        meta = json.loads((out_dir / files["episode"]).read_text(encoding="utf-8"))
        meta["stem"], meta["files"] = stem, files  # trust the computed names, not an older metadata file
        meta["cached"] = True
        meta["out_dir"] = str(out_dir)
        log(f"cached: {out_dir / files['transcript']}")
        return meta

    # 3. find the YouTube upload and download captions
    if url is None:
        videos = list_channel_videos(show["youtube"], args.max_videos)
        vid = match_video(item, videos, pattern)
        url = vid["url"]
        log(f"matched video: {vid['id']}  {vid['title']}")
        info = video_info(url)
    yt_title = info.get("title", "")
    chapters = [{"title": c["title"], "start_time": float(c["start_time"])} for c in (info.get("chapters") or [])]

    with tempfile.TemporaryDirectory() as tmp:
        vtt, lang, source = download_captions(url, Path(tmp), langs)
        log(f"captions: {source} ({lang})")
        shutil.copyfile(vtt, out_dir / files["captions"])
    lines = parse_vtt(out_dir / files["captions"])
    if not lines:
        die("captions file parsed to zero lines")
    body = build_transcript(lines, chapters)
    word_count = sum(len(t.split()) for _, t in lines)

    meta = {
        "show": slug,
        "show_name": (show or {}).get("name") or info.get("channel"),
        "episode_number": num,
        "title": title,
        "youtube_title": yt_title,
        "date": date,
        "duration_sec": (item or {}).get("duration_sec") or info.get("duration"),
        "audio_url": (item or {}).get("audio_url"),
        "description": (item or {}).get("description") or info.get("description"),
        "youtube_id": info.get("id"),
        "youtube_url": f"https://www.youtube.com/watch?v={info.get('id')}",
        "apple_url": (show or {}).get("apple"),
        "caption_source": source,
        "caption_lang": lang,
        "chapters": [{"title": c["title"], "start": fmt_ts(c["start_time"]), "start_sec": c["start_time"]} for c in chapters],
        "word_count": word_count,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stem": stem,
        "files": files,
    }
    dur = meta["duration_sec"]
    header = [
        f"# {title}", "",
        f"- Show: {meta['show_name']}", f"- Date: {date}",
        f"- Duration: {fmt_ts(dur) if dur else 'unknown'}",
        f"- YouTube: {meta['youtube_url']}", f"- Apple Podcasts: {meta['apple_url'] or 'n/a'}",
        f"- Captions: YouTube {source} ({lang}); {word_count:,} words", "",
        "> Auto-generated captions: no speaker names (paragraph breaks mark speaker changes), punctuation may be missing, names may be misspelled.", "",
    ]
    (out_dir / files["transcript"]).write_text("\n".join(header) + body, encoding="utf-8")
    (out_dir / files["episode"]).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    meta["cached"] = False
    meta["out_dir"] = str(out_dir)
    return meta


def public(meta: dict) -> dict:
    d = {k: v for k, v in meta.items() if k not in ("description", "chapters")}
    d["chapter_count"] = len(meta.get("chapters", []))
    d["paths"] = {k: str(Path(meta["out_dir"]) / v) for k, v in meta["files"].items()}
    d["paths"] = {k: v for k, v in d["paths"].items() if k != "summary" or Path(v).exists()}
    return d


def cmd_shows(args, registry):
    print(json.dumps([{"slug": k, **{x: v.get(x) for x in ("name", "feed", "youtube", "apple")}}
                      for k, v in registry.items()], indent=2, ensure_ascii=False))


def cmd_episodes(args, registry):
    show = load_show(registry, args.show)
    items = load_feed(show["feed"])[: args.limit]
    pattern = show.get("episode_regex")
    rows = [{"index": i, "episode_number": episode_number(it["title"], pattern), "date": it["date"],
             "duration_sec": it["duration_sec"], "title": it["title"]} for i, it in enumerate(items)]
    if args.format == "text":
        for r in rows:
            n = f"#{r['episode_number']}" if r["episode_number"] is not None else "-"
            print(f"{r['index']:>3}  {n:>5}  {r['date']}  {r['title']}")
    else:
        print(json.dumps(rows, indent=2, ensure_ascii=False))


def cmd_fetch(args, registry):
    meta = do_fetch(args, registry)
    print(json.dumps(public(meta), indent=2, ensure_ascii=False))


def cmd_transcript(args, registry):
    meta = do_fetch(args, registry)
    sys.stdout.write((Path(meta["out_dir"]) / meta["files"]["transcript"]).read_text(encoding="utf-8"))


def run_summary_engine(prompt: str, engine: str | None, model: str | None, words: int) -> str:
    import subprocess
    if engine is None:
        engine = "claude" if shutil.which("claude") else "openai" if os.environ.get("OPENAI_API_KEY") else None
    if engine is None:
        die("no summary engine: install the `claude` CLI, set OPENAI_API_KEY, or use --print-prompt")
    if engine == "claude":
        if not shutil.which("claude"):
            die("`claude` CLI not found; use --engine openai or --print-prompt")
        cmd = ["claude", "-p", "--output-format", "text"] + (["--model", model] if model else [])
        log(f"running {' '.join(cmd)} on {words:,} words (takes a few minutes)")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env)
        if res.returncode != 0 or not res.stdout.strip():
            die(f"claude failed (exit {res.returncode}): {res.stderr.strip()[:500]}")
        return res.stdout
    if engine == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            die("OPENAI_API_KEY is not set")
        model = model or os.environ.get("OPENAI_MODEL")
        if not model:
            die("give --model or set OPENAI_MODEL (e.g. a current gpt-* model)")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        log(f"calling OpenAI {model} on {words:,} words (takes a few minutes)")
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(f"{base}/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            die(f"OpenAI HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            die(f"unexpected OpenAI response: {json.dumps(data)[:500]}")
    die(f"unknown engine '{engine}'")


def cmd_summarize(args, registry):
    meta = do_fetch(args, registry)
    out_dir = Path(meta["out_dir"])
    summary_path = out_dir / meta["files"]["summary"]
    transcript = (out_dir / meta["files"]["transcript"]).read_text(encoding="utf-8")
    small_meta = {k: meta.get(k) for k in ("show_name", "episode_number", "title", "date", "duration_sec",
                                            "youtube_url", "apple_url", "description")}
    prompt = SUMMARY_PROMPT.format(
        template=TEMPLATE.read_text(encoding="utf-8").strip(),
        meta=json.dumps(small_meta, indent=2, ensure_ascii=False),
        chapters="\n".join(f"- {c['title']} [{c['start']}]" for c in meta["chapters"]) or "(no chapters; segment by topic)",
        transcript=transcript)
    if args.print_prompt:
        sys.stdout.write(prompt)
        return
    if summary_path.exists() and not args.force:
        log(f"cached: {summary_path}")
    else:
        text = run_summary_engine(prompt, args.engine, args.model, meta["word_count"])
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
        summary_path.write_text(text.strip() + "\n", encoding="utf-8")
    d = public(meta)
    d["paths"]["summary"] = str(summary_path)
    print(json.dumps(d, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(prog="podcast", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("shows", help="list registered shows").set_defaults(fn=cmd_shows)

    p = sub.add_parser("episodes", help="list recent episodes of a show")
    p.add_argument("show", help="show slug (see `shows`)")
    p.add_argument("--limit", type=int, default=20, help="feed items to list (default 20)")
    p.add_argument("--format", choices=["json", "text"], default="json", help="json (default) or aligned text")
    p.set_defaults(fn=cmd_episodes)

    def episode_args(p):
        p.add_argument("show", help="show slug (see `shows`), or '-' when EPISODE is a YouTube URL")
        p.add_argument("episode", help="latest | latest-N | number | YouTube URL | words from the title")
        p.add_argument("--out", help="output directory (default: <project>/output/<show>)")
        p.add_argument("--force", action="store_true", help="re-download even if cached")
        p.add_argument("--recent", action="store_true", help="if title words match several episodes, take the newest")
        p.add_argument("--max-videos", type=int, default=30, help="recent channel videos to scan for a match")
        p.add_argument("--langs", default="en-orig,en", help="caption languages in preference order")

    p = sub.add_parser("fetch", help="download captions and write transcript + metadata (JSON result)")
    episode_args(p); p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("transcript", help="fetch if needed, then print the transcript markdown")
    episode_args(p); p.set_defaults(fn=cmd_transcript)

    p = sub.add_parser("summarize", help="fetch if needed, then write the summary (claude CLI or OpenAI API, see --engine)")
    episode_args(p)
    p.add_argument("--engine", choices=["claude", "openai"], help="summary engine (default: claude CLI if installed, else openai)")
    p.add_argument("--model", help="model for the engine (claude: optional; openai: required, or set OPENAI_MODEL)")
    p.add_argument("--print-prompt", action="store_true", help="print the summary prompt instead of running an engine (use it with any model)")
    p.set_defaults(fn=cmd_summarize)

    args = ap.parse_args()
    registry = yaml.safe_load(REGISTRY.read_text()) if REGISTRY.exists() else {}
    args.fn(args, registry)


if __name__ == "__main__":
    main()
