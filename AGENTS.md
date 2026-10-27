# AGENTS.md

Guidance for AI agents (Claude Code, Codex, Cursor, OpenClaw, etc.) working in this repository.

## What is here

A CLI, `bin/podcast`, that turns a podcast episode into a transcript and a summary. Episodes are resolved from the show's RSS feed and matched to the YouTube upload; captions come from YouTube. See `README.md` for the human-oriented overview.

## Where things are

- `src/podcast_digest/`: the CLI (`cli.py`), built-in show registry (`podcasts.yaml`) and `SUMMARY_TEMPLATE.md`. Plain Python package, no Claude dependency.
- `.claude/skills/podcast-transcript/SKILL.md`: Claude Code skill that points at the tool. Other agents can ignore it.
- `output/<show>/`: summaries at the top, `transcripts/` below.

## How to call it

Inside this repo, go through `bin/podcast`: it runs the CLI from the checkout with `uv` and keeps output under the repo's `output/` whatever the current directory. (Outside the repo, the installed `podcast` command is the same CLI; it writes to `./output/`.) Results are JSON on stdout, progress logs on stderr.

```bash
bin/podcast shows                                   # discover show slugs
bin/podcast episodes <show> --limit 20              # discover episodes (index, number, date, title)
bin/podcast fetch <show> <episode>                  # transcript + metadata; JSON includes absolute "paths"
bin/podcast transcript <show> <episode>             # transcript markdown on stdout
bin/podcast summarize <show> <episode>              # summary via claude CLI or OpenAI API; JSON with paths
bin/podcast summarize <show> <episode> --print-prompt   # summary prompt on stdout, for any model
```

`<episode>` accepts `latest`, `latest-N`, a number (`304`, `#304`, `ep304`), a YouTube URL, or words from the title. Prefer numbers once you know them; they are unambiguous. With a URL you may pass `-` as the show: the tool detects a registered show from the video's channel and fills metadata from its feed, so URL and numbered fetches produce the same files and share the cache. Only an unregistered channel falls back to a folder named after the channel.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | success | read `paths` from the JSON |
| 1 | error (unknown show, no match, network) | message is on stderr |
| 2 | video has no captions | report it; `audio_url` in the metadata can be transcribed another way |
| 3 | title words matched several episodes | JSON lists `candidates`; retry with the episode number or add `--recent` |

## Writing the summary yourself

The standard flow for any agent, whatever model it runs on, is: `fetch` (or `transcript`), read the transcript, write the summary. The `summarize` subcommand is optional: `--engine claude` needs the `claude` CLI logged in, `--engine openai` needs `OPENAI_API_KEY` plus `--model` or `OPENAI_MODEL` (`OPENAI_BASE_URL` for compatible APIs), and `--print-prompt` gives you the prompt for any other model. If none applies, write the summary yourself. No API key is needed to fetch transcripts. Follow `src/podcast_digest/SUMMARY_TEMPLATE.md` exactly and save it as `output/<show>/<stem>-summary.md`, where `stem` is in the fetch JSON. Transcripts, raw captions and episode metadata live in `output/<show>/transcripts/`; the fetch JSON's `paths` has the absolute locations. Keep the headings exactly; summaries across episodes are meant to be comparable.

Transcripts are large (a 2-hour episode is 20k to 27k words). Read them in chunks. Chapter headers (`## Title [mm:ss]`) come from the YouTube chapters and are the intended spine of the summary.

## Conventions

- Generated files go only under `output/<show>/`. Do not commit them unless asked.
- Never edit `transcripts/*-captions.vtt`; it is the raw source.
- `fetch` is idempotent and cached per episode. Use `--force` only when the captions must be re-downloaded.
- To add a show, append to `src/podcast_digest/podcasts.yaml` (built-in list, see README); users of the installed CLI use `~/.config/podcast-digest/podcasts.yaml` instead. Find the feed URL via the iTunes search API, not by guessing.
- `output/**/transcripts/` is git-ignored: raw captions and full transcripts are the podcast's copyrighted content and must not be published. Summaries may be committed.
- Do not install `yt-dlp` or other dependencies globally; the script declares them inline for `uv`.
