# Buzzer Classification

Local, end-to-end pipeline for collecting TikTok, Instagram, X, Facebook and YouTube comments and
building features to classify "buzzer" (coordinated/inauthentic) accounts. Everything lives in
one notebook: `buzzer.ipynb`, with offline regression tests in `tests/`.

The [code and feature review](CODE_REVIEW.md) documents the fixes, column critique, proposed
feature baseline, and evaluation plan. The saved sample has only one post per platform and
mostly single-comment accounts; it is not sufficient to validate a buzzer classifier.

The standardized [13-feature schema and preprocessing guide](BASELINE_SCHEMA.md) provides
intuitive names, mappings from notebook columns, and policies for platform-wide missing data.
Its machine-readable contract is [behavioral_baseline.schema.json](behavioral_baseline.schema.json).

## What "end-to-end" means here

- **YouTube**: pure API call, runs unattended.
- **TikTok / Instagram / X / Facebook**: each uses a real browser session and scrolling.
  The browser cell
  opens a visible Chromium window per platform, **pauses and waits for you** to log in / scroll,
  then resumes automatically once you press Enter in the notebook. All four platforms share the
  same `Harvester` class and scroll/batch/coverage logic — only endpoint patterns, comment-panel
  selectors, reply-button text and the payload parser differ per platform.
- Login is a one-time cost per platform — each browser profile persists to disk
  (`browser_profile/<platform>/`), so subsequent runs usually just confirm the session is still
  valid.

Run the whole notebook with "Run All"; it stops only where a human is genuinely required, then
proceeds on its own.

## Setup

```bash
pip install playwright google-api-python-client "pandas>=2.2,<3" numpy langdetect python-dotenv scikit-learn
playwright install chromium
```

Set your YouTube API key via a `.env` file — **never commit it**:

```
YOUTUBE_API_KEY="..."
```

The notebook loads `.env` without overriding existing environment variables. If the key is
absent, YouTube collection is skipped and browser-only collection can continue.

Run the offline checks with `python -m unittest discover -s tests -v`.

No API keys/tokens are needed for TikTok, Instagram, X, or Facebook — those four are collected
via the logged-in browser session (cookies persisted in `browser_profile/<platform>/`), not an
API.

## Notebook structure

1. **Environment** — loads credentials.
2. **What to collect** — the only cell you need to edit: YouTube video IDs and
   `BROWSER_TARGETS` (post URLs for TikTok/Instagram/X/Facebook), plus per-target scroll tuning.
3. **Canonical schema** — shared text normalization/schema across every platform.
4. **YouTube collection** — unattended API pull.
5. **Browser collection** — the manual-pause step described above, shared across TikTok,
   Instagram, X and Facebook. Captures the network JSON the comment section actually loads (not
   DOM parsing).
6. **Parse captured payloads** — turns raw browser JSON into structured rows per platform. Also
   runs profile enrichment for TikTok and Instagram (followers/following/posts/verified/bio) for
   each unique commenter, with stable-ID checks and a seven-day profile cache. X needs no
   separate enrichment pass — its comment payload already embeds profile stats inline. Facebook
   profile enrichment isn't implemented (see Known gaps).
7. **Assemble & derive features** — coordination signals (`thread_co_*`,
   `corpus_duplicate_count`, etc.) and account-level features (`follower_to_following_ratio`,
   `bio_length`, `handle_digit_suffix`, `account_age_days`, ...).
8. **Pseudonymise and export** — hashes identity columns for shareable output; keep the salt
   private.
9. **Checklist** — operational notes (see below).

## Outputs

- `canonical/*.csv` — one row per comment, per platform, overwritten each run.
- `features/dataset.csv` — one row per **comment** (keeps text/bio for labelling and audit).
- `features/dataset_accounts.csv` — one row per **account** (`global_user_id`), the grain the
  classifier trains on.
- `*_public.csv` siblings of both — pseudonymised, identities and comment/source locators hashed,
  free text, URLs, real names, and exact timestamps dropped,
  still joinable on hashed `global_user_id`.
- `raw/*.jsonl` — raw captured payloads, appended (not overwritten) across runs.
- `features/feature_quality.csv` — per-platform missingness and constant-column diagnostics.
- `features/baseline_accounts.csv` — the 13 standardized behavioral columns plus account,
  window, support, and per-feature `__status` metadata; `baseline_accounts_public.csv` hashes account IDs.
- `features/baseline_feature_quality.csv` — per-platform observed/null counts and missingness reasons.
- `features/baseline_accounts.jsonl` — strict schema records when both UTC observation-window
  boundaries are configured in §2; empty in exploratory archive mode to prevent stale-window reuse.
- `raw/collection_runs.jsonl` — browser target/session counts, cap, sampling method, and stop reason.

Canonical browser files replay the entire raw archive, including previous targets. An overwrite
does not restrict them to the current target list. Filter source posts and time windows explicitly
when defining a modeling cohort. New CSVs use an explicit null marker and should be loaded with
`read_csv_with_lists` to preserve IDs, lists, and missing values.

## Key conventions

- **`global_user_id`** = `<platform>:<user_id>` — all aggregation and coordination features key
  on this, never the raw platform user id.
- **`source_post_id`** identifies the source video/post; the legacy **`post_id`** identifies the
  comment/tweet. **`parent_comment_id`** preserves the direct reply relationship.
- TikTok now prefers stable `uid` over `sec_uid`. Rebuild canonical rows from raw and migrate
  any old account labels deliberately; old and new account/public identifiers are not interchangeable.
- **`thread_id`** = one comment thread (top-level comment + its replies) on TikTok, Instagram,
  Facebook and YouTube. X uses `conversation_id_str` instead and retains the direct parent
  separately, so coordination features still work but
  an X "thread" can legitimately be larger than one on the other platforms.
- **Breadth over depth**: comments are capped per post (default 500) rather than scraped to
  exhaustion. Add more posts instead of raising the cap — coordination signals fire when the
  same accounts recur *across* posts.
- Browser reported totals are unverified hints. Caps, unique-comment plateaus, and batch ceilings
  stop scrolling; `target_coverage` remains a compatibility setting without percentage-based stopping.
- `post_co_*` measures recurrence across source posts, while `thread_co_*` measures threads.
  Pair budgets produce explicit missing values and skipped-group counts if work is omitted.
- `BASELINE_ACCOUNT_FEATURE_COLUMNS` lists the proposed behavioral baseline;
  `PROFILE_ACCOUNT_FEATURE_COLUMNS` defines a separate profile experiment. No model is trained here.
- Notebook §7b implements `make_baseline_model`: `shared_median`, `platform_median`, and `native`.
  Fit only on labeled training-fold account tables. Platform-median fallback requires explicit
  `allow_pooled_fallback=True`; preprocessing never writes imputed values into the collected data.
- Profile audit metadata now includes `profile_visibility` (`public`/`private`/`unknown`),
  `profile_access_status` (`readable`/`restricted`/`failed`, or null when unchecked), and
  `profile_checked_at` (UTC). TikTok uses an explicit privacy flag on an identity-matched profile;
  failed requests never establish privacy. These fields are excluded from model features.

## Where things stand / known gaps

- **Instagram, X and Facebook parsers are best-effort.** None of the three publish their
  private/GraphQL comment payload shape, and it can change without notice. If a run captures
  payloads but parses 0 comments, `inspect_payloads('<platform>')` prints the raw captured JSON
  so you can see the actual shape and adjust `parse_instagram`/`parse_x`/`parse_facebook`.
  Facebook is the least stable of the three — its GraphQL doc_ids rotate constantly.
- **Facebook profile enrichment isn't implemented.** Personal-profile friend/follower counts are
  usually hidden from non-friends regardless of login, and reliably telling a Page (which does
  expose a public follower count) apart from a Person needs more DOM inspection than is
  justified right now. `followers_count`/`following_count`/`bio_text` stay null for Facebook.
- TikTok profile enrichment is best-effort — TikTok's embedded page-data shape has changed before
  and may again; if enrichment yields nothing, inspect `__UNIVERSAL_DATA_FOR_REHYDRATION__` on a
  live profile page and adjust `_extract_profile_from_page`.
- `has_custom_avatar` stays null on TikTok, YouTube, Instagram, and Facebook because no validated
  default-avatar classifier is available. X's URL heuristic remains audit-only, outside model features.
- `account_created_at` is now used, via the derived `account_age_days` feature — real for
  YouTube (channel `publishedAt`) and X (profile `created_at`), structurally null for
  TikTok/Instagram/Facebook (none expose account-creation date publicly).
- `repost_count`/`is_repost` are populated for X (`retweet_count`, text starting `RT @`) but not
  yet a standalone feature — `duplicate_text_count`/`thread_co_*` already capture coordinated
  amplification generically.
- `source_device` is excluded from features — no platform here exposes posting-device info in a
  form this pipeline captures.
- `following_count` is available through TikTok/Instagram enrichment and inline on X, but has
  no captured YouTube equivalent; Facebook profile enrichment is unimplemented. Models accepting
  nulls do not solve platform-dependent missingness bias.
- Full-corpus feature exports are exploratory. Recompute coordination/repetition inside the
  permitted training/inference context of each fold; keep accounts/campaigns together and evaluate
  on held-out topics or time periods. See the review for label and feature-selection requirements.

## Ethics / ToS

Only public-page reading is performed (comments, profile pages) — no automated login, no CAPTCHA
bypass, no bot-detection evasion, on any of the five platforms. Keep the pseudonymisation salt
out of anything committed or shared. Confirm institutional review requirements before
collecting/publishing data.

## Not committed

`.env` (API keys/tokens) and `buzzer_data/` (raw collected data) are gitignored and stay local.

## Current scraping output

All five platforms export exactly `like_count`, `reply_count`, and `date_published`
(comment publication time, UTC) to `buzzer_data/canonical/<platform>.csv`.
Section 7 combines these into `buzzer_data/features/dataset.csv` with the same columns.
Unknown values remain `\N`, not zero. Profile enrichment and legacy account-feature/public
exports are skipped. Raw payload archives remain local for deduplication and rebuilding;
existing historical exports are not deleted. Earlier account-baseline documentation describes
legacy functions, not the current three-column output.
