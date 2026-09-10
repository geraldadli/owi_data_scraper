# Buzzer Classification

Local, end-to-end pipeline for collecting TikTok and YouTube comments and building features to classify "buzzer" (coordinated/inauthentic) accounts. Everything lives in one notebook: `buzzer.ipynb`.

## What "end-to-end" means here

- **YouTube**: pure API call, runs unattended.
- **TikTok**: requires a real login and real scrolling. There's no way to script around that without violating platform terms (credential automation, fingerprint spoofing, CAPTCHA bypass), so the notebook doesn't try. Instead, the TikTok cell opens a visible Chromium window, **pauses and waits for you** to log in / scroll, then resumes automatically once you press Enter in the notebook.
- Login is a one-time cost — the browser profile persists to disk (`browser_profile/`), so subsequent runs usually just confirm the session is still valid.

Run the whole notebook with "Run All"; it stops only where a human is genuinely required, then proceeds on its own.

## Setup

```bash
pip install playwright google-api-python-client pandas langdetect
playwright install chromium
```

Set your YouTube API key (and TikTok/X tokens if used) via a `.env` file — **never commit it**:

```
YOUTUBE_API_KEY="..."
```

## Notebook structure

1. **Environment** — loads credentials.
2. **What to collect** — the only cell you need to edit: YouTube video IDs and TikTok post URLs (`BROWSER_TARGETS`), plus `max_scroll_rounds`.
3. **Canonical schema** — shared text normalization/schema across both platforms.
4. **YouTube collection** — unattended API pull.
5. **TikTok collection** — the manual-pause step described above. Captures the network JSON the comment section actually loads (not DOM parsing).
6. **Parse captured payloads** — turns raw browser JSON into structured rows. Also runs TikTok profile enrichment (followers/following/posts/verified/bio) for each unique commenter, cache-aware so re-runs don't re-visit known accounts.
7. **Assemble & derive features** — coordination signals (`thread_co_*`, `corpus_duplicate_count`, etc.) and account-level features (`follower_to_following_ratio`, `bio_length`, `handle_digit_suffix`, ...).
8. **Pseudonymise and export** — hashes identity columns for shareable output; keep the salt private.
9. **Checklist** — operational notes (see below).

## Outputs

- `canonical/*.csv` — one row per comment, per platform, overwritten each run.
- `features/dataset.csv` — one row per **comment** (keeps text/bio for labelling and audit).
- `features/dataset_accounts.csv` — one row per **account** (`global_user_id`), the grain the classifier trains on.
- `*_public.csv` siblings of both — pseudonymised, identity hashed, text/bio/real-name dropped, still joinable on hashed `global_user_id`.
- `raw/*.jsonl` — raw captured payloads, appended (not overwritten) across runs.

## Key conventions

- **`global_user_id`** = `<platform>:<user_id>` — all aggregation and coordination features key on this, never the raw platform user id.
- **`thread_id`** = one comment thread (top-level comment + its replies) on both platforms — coordination features depend on this staying consistent.
- **Breadth over depth**: comments are capped per video/post (default 500) rather than scraped to exhaustion. Add more videos/posts instead of raising the cap — coordination signals fire when the same accounts recur *across* videos.

## Where things stand / known gaps

- TikTok profile enrichment is best-effort — TikTok's embedded page-data shape has changed before and may again; if enrichment yields nothing, inspect `__UNIVERSAL_DATA_FOR_REHYDRATION__` on a live profile page and adjust `_extract_profile_from_page`.
- `has_custom_avatar` is collected but **not** used as a feature — TikTok's CDN paths changed and the default-avatar marker no longer matches, so it's currently non-discriminative.
- `account_created_at`, `source_device`, `repost_count` are excluded from features — platform-structural gaps, not fixable by more scraping.
- `following_count` has no YouTube equivalent (channels don't expose it); LightGBM handles the resulting nulls natively rather than imputing.

## Ethics / ToS

Only public-page reading is performed (comments, profile pages) — no automated login, no CAPTCHA bypass, no bot-detection evasion. Keep the pseudonymisation salt out of anything committed or shared. Confirm institutional review requirements before collecting/publishing data.

## Not committed

`.env` (API keys/tokens) and `buzzer_data/` (raw collected data) are gitignored and stay local.
