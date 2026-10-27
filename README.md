# podcast-digest

Podcast episode in, clean transcript and structured summary out. One command, from the terminal, by hand or driven by any AI agent.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

![demo](docs/demo.gif)

The registered shows, the episodes on the feed, a cached fetch, and the summary it produced.

## Quick start

```bash
uv tool install git+https://github.com/garyzava/podcast-digest   # or: pipx install git+https://github.com/garyzava/podcast-digest
podcast episodes moonshots --limit 5 --format text   # what's on the feed
podcast fetch moonshots latest                       # transcript + metadata -> ./output/moonshots/
podcast summarize moonshots latest                   # summary via claude CLI or OPENAI_API_KEY
podcast summarize moonshots latest --print-prompt    # or: paste the prompt into any model
```

Needs [`uv`](https://docs.astral.sh/uv/) (or pipx) and network access; `uv` brings Python 3.11 and the two dependencies (`yt-dlp`, `pyyaml`) with it. To try it without installing: `uvx --from git+https://github.com/garyzava/podcast-digest podcast shows`. Inside a clone, `bin/podcast` runs the same CLI from the checkout.

## Why this one

- **Feed-matched episodes.** Episodes come from the show's RSS feed, the same list Apple Podcasts shows, and are matched to the YouTube upload by `#NNN`, then by fuzzy title. `latest`, `304`, a YouTube URL or words from the title all resolve to the same cached files.
- **Agent-friendly.** JSON on stdout, logs on stderr, exit codes `0/1/2/3`, a `candidates` list when title words are ambiguous, and absolute `paths` in every result.
- **Model-agnostic summaries.** Use the `claude` CLI, the OpenAI API, or `--print-prompt` to feed any model. Every summary follows one template, so episodes are comparable.
- **Copyright-safe layout.** Transcripts and raw captions go to a git-ignored `transcripts/` folder. Only your summaries are committable.

## Example summary

[Moonshots #305: SpaceX's AI Business Up 830%, OpenAI Agents Lie, and Startup Turns Mercury Into Gold](output/moonshots/moonshots-ep305-2026-10-23-summary.md) and [Moonshots #304 with Chamath Palihapitiya](output/moonshots/moonshots-ep304-2026-10-21-summary.md).

> **TL;DR** The hosts work through SpaceX's earnings call, where AI compute rather than rockets is now the largest business, with Elon Musk targeting 10 to 20 gigawatts of new compute in 2027, a 440,000-chip Vera Rubin training cluster, Terafab, 200 gigawatts a year of solar, and a possible SpaceX-Tesla merger. Wissner-Gross argues Musk is rewriting corporate governance by treating Tesla and SpaceX as one porous federation. ...
>
> **SpaceX's AI Business Grows 830 [10:45]**
> - Musk says power, cooling, and infrastructure, not chips, constrain superintelligence deployment; he expects 10 gigawatts of incremental compute in 2027 on top of roughly 3 gigawatts by year end [12:58].
> - ...

## Usage

```bash
podcast shows                                        # registered shows
podcast episodes moonshots --limit 10 --format text  # browse recent episodes
podcast fetch moonshots latest                       # newest episode
podcast fetch moonshots latest-1                     # second newest
podcast fetch moonshots 304                          # by episode number ("#304", "ep304" also work)
podcast fetch moonshots "mercury into gold"          # words from the title
podcast fetch moonshots chamath --recent             # several matches: take the newest
podcast fetch moonshots https://www.youtube.com/watch?v=ID   # by URL; metadata filled from the feed
podcast fetch - https://www.youtube.com/watch?v=ID    # URL only; show auto-detected from the channel
podcast transcript moonshots 304                     # print the transcript to stdout
podcast summarize moonshots 304                      # fetch + summary
podcast summarize moonshots 304 --print-prompt       # emit the summary prompt instead
podcast --help
```

`fetch`, `transcript` and `summarize` share the same options: `--out DIR`, `--force` (re-download), `--recent`, `--max-videos N`, `--langs en-orig,en`.

## Summaries

| Engine | Needs | Notes |
|---|---|---|
| `--engine claude` | the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI, logged in | no API key; `--model` optional |
| `--engine openai` | `OPENAI_API_KEY`, plus `--model` or `OPENAI_MODEL` | `OPENAI_BASE_URL` for compatible APIs |
| `--print-prompt` | nothing | prompt on stdout; paste it into any model |

Default: `claude` if the CLI is installed, else `openai` if the key is set. Every summary follows [`src/podcast_digest/SUMMARY_TEMPLATE.md`](src/podcast_digest/SUMMARY_TEMPLATE.md): TL;DR, chapter by chapter with `[mm:ss]` timestamps, key takeaways, claims and numbers, names, open questions.

## Output

Files land in `./output/<show>/` under the current directory (change it with `--out DIR` or `PODCAST_DIGEST_OUT`), named `<show>-ep<NNN>-<YYYY-MM-DD>-*`. Summaries sit at the top; the bulky source files go in `transcripts/`:

| File | Content |
|---|---|
| `<stem>-summary.md` | TL;DR, chapter-by-chapter, key takeaways, claims and numbers, names, open questions |
| `transcripts/<stem>-transcript.md` | cleaned transcript: chapter headers, `[mm:ss]` per paragraph, paragraph breaks on speaker changes |
| `transcripts/<stem>-captions.vtt` | raw captions as downloaded |
| `transcripts/<stem>-episode.json` | title, date, duration, audio URL, YouTube id, chapters, caption source, word count |

Fetch is cached per episode: a repeat run re-reads the feed but never touches YouTube, and returns `"cached": true`.

YouTube auto-captions are the source when the channel uploads no manual captions. They usually have punctuation and speaker-change markers but no speaker names, and proper nouns can be misspelled. Episodes without any captions exit with code 2; the `audio_url` in the metadata can then be transcribed with Whisper (not included).

## Adding a show

Add a block to `~/.config/podcast-digest/podcasts.yaml` (create the file; same format as the built-in [`src/podcast_digest/podcasts.yaml`](src/podcast_digest/podcasts.yaml), and your entries win on the same slug):

```yaml
myshow:
  name: My Show
  feed: https://feeds.example.com/myshow        # find it: https://itunes.apple.com/search?term=my+show&media=podcast
  youtube: https://www.youtube.com/@myshow/videos
  apple: https://podcasts.apple.com/us/podcast/id123456   # optional
  episode_regex: '#\s*(\d+)'                     # one capture group for the episode number
```

## For AI agents

Agents of any model: read [`AGENTS.md`](AGENTS.md) for the commands, exit codes and how to write the summary yourself. Claude Code users get the same via the `podcast-transcript` skill in `.claude/skills/`.

## Layout

```
src/podcast_digest/cli.py               the CLI (`podcast` entry point in pyproject.toml)
src/podcast_digest/podcasts.yaml        built-in show registry
src/podcast_digest/SUMMARY_TEMPLATE.md  the summary format every summary follows
bin/podcast                             in-repo shim: runs the CLI from the checkout with uv
output/<show>/                          summaries at the top, transcripts/ below (git-ignored)
docs/                                   demo recording and its VHS tape
```

## License

MIT. Summaries in `output/` are derived works written here; transcripts and captions are the podcast's content and are never committed.
