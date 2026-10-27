---
name: podcast-transcript
description: Fetch the full transcript of a podcast episode (from YouTube captions, matched against the show's RSS/Apple Podcasts feed) and write a chapter-by-chapter summary with key takeaways. Use this whenever the user asks for a podcast transcript, episode summary, show notes, "what did they say in episode N", key points from a YouTube interview, or wants to analyze a podcast such as Moonshots — even if they only mention the show name and an episode.
---

# Podcast transcript + summary

The tool lives in `src/podcast_digest/` (plain Python package, usable by any agent); this skill only tells you how to drive it. Full usage: `README.md` and `AGENTS.md` at the project root.

## 1. Fetch the transcript

```bash
bin/podcast shows                                        # registered shows
bin/podcast episodes moonshots --limit 10 --format text  # browse recent episodes
bin/podcast fetch moonshots latest                       # newest; latest-1 = second newest
bin/podcast fetch moonshots 304                          # by number ("#304", "ep304" also work)
bin/podcast fetch moonshots "mercury into gold"          # words from the title (fuzzy)
bin/podcast fetch moonshots chamath --recent             # several matches: take the newest
bin/podcast fetch moonshots https://www.youtube.com/watch?v=ID   # by URL (show auto-detected if you pass -)
bin/podcast transcript moonshots 304                     # transcript markdown on stdout
bin/podcast summarize moonshots 304                      # unattended: claude CLI by default, --engine openai with OPENAI_API_KEY
```

"Latest", "second latest" map to `latest`, `latest-1`. Episode order comes from the RSS feed (what is "on air"); the YouTube upload is matched on `#NNN`, then fuzzy title. The JSON result carries absolute `paths`. Exit 3 = ambiguous title words (candidates listed; retry by number or add `--recent`). Exit 2 = no captions at all; tell the user and suggest Whisper on `audio_url`. Fetch is cached per episode (`"cached": true`); `--force` re-downloads.

Layout under `output/<show>/`: the summary `<stem>-summary.md` at the top, and `transcripts/<stem>-transcript.md`, `-captions.vtt`, `-episode.json` below it. Stem = `<show>-ep<NNN>-<YYYY-MM-DD>`.

Caption quality: YouTube auto-captions carry no speaker names (paragraph breaks mark speaker changes), may lack punctuation, and mangle proper nouns. Cross-check odd names against `description` in the episode JSON and say so in the summary header.

## 2. Write the summary

When you are the model working in-session, write the summary yourself (calling `summarize` spawns a second Claude run and costs the tokens again). Read `transcripts/<stem>-transcript.md` fully, in chunks of ~600 lines for 2-hour episodes. Use the YouTube chapters as the spine; they are the host's own segmentation and keep the summary navigable with timestamps. If you must run it unattended, `summarize --engine openai` works outside Claude Code when `OPENAI_API_KEY` and `OPENAI_MODEL` are set.

Follow `src/podcast_digest/SUMMARY_TEMPLATE.md` exactly (same headings, same order) so summaries across episodes are comparable, and save to `output/<show>/<stem>-summary.md`. Plain prose, no hype, paraphrase rather than quote since captions are not verbatim-reliable, keep numbers and names. Finish by telling the user the summary path, the transcript path and the word count.

## Adding a show

Append to `src/podcast_digest/podcasts.yaml` (or `~/.config/podcast-digest/podcasts.yaml` for a local-only show); find the feed with
`curl -s "https://itunes.apple.com/search?term=<show name>&media=podcast&limit=5" | jq '.results[] | {collectionName, feedUrl}'`
and use the channel's `/videos` URL. `episode_regex` needs one capture group (`#\s*(\d+)` covers "EP #304", "| #303").
