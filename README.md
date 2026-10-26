# cryptocardhub — public source

The content pipeline behind **[defycard.com](https://defycard.com)**: an autonomous system that
researches crypto-card topics, writes and narrates short-form video, renders it, publishes to
YouTube/TikTok/Instagram, and generates programmatic SEO pages for the site.

This repository is a **published subset** of a larger private working tree. It carries the code and
the templates; it does not carry state, logs, credentials, or the operator's working notes.

This snapshot is refreshed every Monday 07:00 UTC from the private repository, and again right after any change to fee/price data (config/rates.yaml, seo-site/src/data/). Each commit message names the private source commit.

## What is here

| path | what it is |
|---|---|
| `video-generator/pipeline/` | the pipeline: topic selection → script → TTS → visuals → music → captions → render → publish |
| `video-generator/tests/` | the test suite, including the gates described below |
| `video-generator/templates/` | prompt and topic templates |
| `seo-site/` | the Astro static site and its content collections |
| `scripts/` | operational scripts: keyword research, sitemap, deploy, reporting |
| `config/rates.yaml` | cashback rates as a single source of truth |
| `docs/architecture.md` | how the parts fit together |

## What is deliberately not here

`.env` and every credential · `state/*.db` (the dedup ledger and publish log) · `logs/` ·
`backups/` · the operator's agent skills and working instructions · the private repository's
commit history.

**The history is not carried across on purpose.** A public repository cannot un-publish a secret
that a months-old commit contains, so this repository starts from a single reviewed commit rather
than importing a past nobody re-read line by line.

## A note on how the code reads

Comments here are longer than usual and often explain a *defect* rather than a mechanism. That is
deliberate: most non-obvious rules in this codebase were bought with a real failure, and the
comment records what the failure was so the rule is not silently removed later. Examples you will
meet quickly:

* a cause-namer that separates four different zeros, because "no data", "no permission", "found
  nothing" and "everything was already used" once looked identical from outside the module;
* a topic selector whose failure set grows on failure — without it the selector loops by
  construction, which cost 169 refused slots in August 2026;
* rates and country lists compared against the issuer, because an expired promotion was published
  as a current property of the card on 307 of 544 pages.

## Running it

```bash
cp .env.example .env      # fill in real values
cd video-generator && python -m pytest tests -q
```

The pipeline needs API keys for its providers (stock media, TTS, the publishing platforms). The
template lists every variable it reads; none of them ship with a value.

## Licence

No licence is granted. The code is published for inspection, not for reuse.
