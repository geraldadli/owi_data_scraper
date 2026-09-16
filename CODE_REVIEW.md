# Scraping and feature review — 11 September 2026

The collection pipeline had correctness problems that would change account identities, comment coverage, and coordination features. These have been fixed in `buzzer.ipynb`, with offline regression tests in `tests/`. There is no trained classifier or labeled dataset in the repository, so this review proposes a feature baseline; it does not claim to have selected the best predictors empirically.

## Findings in the saved data

The audit read local files without logging in, making collection requests, or changing the saved datasets.

| Existing canonical data | TikTok | YouTube |
|---|---:|---:|
| Comments | 961 | 500 |
| Distinct accounts | 889 | 468 |
| Distinct source posts | 1 | 1 |
| Missing follower statistics | 100% | 0% |
| Missing following statistics | 100% | 100% |
| Missing verification | 100% | 100% |

Across both platforms, **1,263 of 1,357 accounts (93.1%) have only one comment**. No account can show recurrence across sampled source posts on its own platform. Most timing features cannot be estimated; cross-post coordination is constant. A classifier trained on this sample could learn platform, topic, and collection artifacts instead of coordinated behavior.

There are 725 rows in the TikTok profile cache, but no profile statistics in the saved TikTok canonical table. The cache's presence does not establish that those snapshots are current or correctly attached to stable IDs. New enrichment validates profile identities and timestamps before merging.

Replaying all 350 saved TikTok payloads with the revised parser produced **976 distinct comments and 898 accounts**, versus 961 comments in the saved table: **15 additional comments**, including embedded replies. There were zero payload parse errors. This replay was an offline check; the saved canonical and public datasets were preserved.

## Correctness fixes

| Priority | Previous behavior | Revised behavior |
|---|---|---|
| High | Browser target 2 inherited target 1's comment count and total, allowing premature stopping. | Each target has independent coverage state and cap; raw payloads flush per target. |
| High | The cap was checked only after large scroll/reply batches; normalisation did not cap their overshoot. | Scroll and reply loops check the cap; canonical construction enforces the cap per capture session and target. A network response can still contain extra comments in the raw archive. |
| High | Generic nested totals and reply totals were treated as comparable coverage denominators. | Totals are diagnostic hints. They no longer trigger early termination or a claim of verified completeness. |
| High | CSV inference could round large IDs or turn handles such as `NA` into nulls. | ID columns use string dtypes; default NA token inference is disabled. New CSVs use `\\N` as an explicit null marker. |
| High | Missing users could become one `<platform>:nan` account; repeated snapshots inflated counts. | Authorless comments remain canonical but are excluded from account features; latest comment snapshots are deduplicated by platform and comment ID. |
| High | YouTube used a comment-thread resource ID as the comment ID and fetched only embedded reply subsets. | Top-level comment IDs and parent IDs are distinct; `comments.list` paginates replies under the shared cap. API errors do not quietly replace the previous canonical file with a partial collection. |
| High | Splitting source URLs at `?` collapsed every YouTube video into `/watch`. | Explicit `source_post_id` preserves video/post identity across supported URL shapes. Unknown shapes remain missing. |
| High | Coordination keyed only on raw thread ID; different platforms could share a group. Top-level commenters on the same post were usually in separate groups. | Group keys include platform and source post. Separate `post_co_*` features measure repeated participation across posts. |
| High | Large groups silently received zero coordination. Global pair storage was unbounded. | A group-size limit and pair-event budget bound exact pairing. Affected accounts expose skipped-group counts and null coordination values. |
| High | Replaying raw payloads replaced capture time with the current time; account age changed on each run. | Capture timestamps are preserved. Age uses the recorded capture time; unrecorded historical time stays unknown. |
| High | Exported comment IDs still provided public locators despite hashed usernames. | Account, source, comment, parent, and thread IDs receive domain-separated HMACs; raw URLs, free text, and exact timestamps are removed. |
| Medium | TikTok omitted embedded replies; Instagram chose one of the comment lists. | Embedded TikTok replies, both Instagram lists, and preview/child replies are handled with ID deduplication. |
| Medium | X recursive parsing included conversation roots, quoted tweets, and unrelated results. Facebook feedback IDs were treated as reply parents. | X filters to the configured root conversation/direct replies and excludes nested quotes; Facebook uses actual comment parents and requires comment-shaped nodes. |
| Medium | False verification became null; unavailable biography and username became zero-valued features. | Nullable booleans preserve false versus unknown. Biography/handle features remain null when unavailable; new CSVs preserve known empty biographies. |
| Medium | Profile caches were permanent, username-only, and discarded on forced refresh failure. | Snapshots include stable profile IDs and observation times. Seven-day freshness determines visits; successful prior snapshots survive failed refreshes and are marked stale. Identity mismatches cannot attach fields to another account. |
| Medium | `groupby.first()` could combine profile values from different snapshots. | Account output selects one actual latest snapshot. |
| Medium | `media_count` counted the `text` sentinel, so ordinary text counted as media. A ratio of logarithms was named follower/following ratio. | Text contributes zero media. The smoothed ratio is `(followers+1)/(following+1)`; the log ratio is exported separately. |
| Medium | Empty `custom_verify` hid enterprise verification; stale avatar URL heuristics implied known custom avatars. | TikTok checks both verification fields; unsupported TikTok/YouTube avatar classification stays null. |
| Medium | The environment rejected browser-only runs without a YouTube key and never loaded the advertised `.env`. | `.env` loads without overriding environment variables; absent YouTube credentials skip that collection only. |

The browser launch's `AutomationControlled` suppression argument was also removed to match the notebook's documented behavior.

## Column critique and recommendations

| Columns / family | Role and recommendation |
|---|---|
| `user_id`, `global_user_id`, `username`, `display_name` | Identity, annotation, and grouped splitting only. Never train on raw or hashed IDs. A platform account is not a verified person; do not merge accounts across platforms by handle. TikTok now prefers stable `uid`, with `sec_uid` only as fallback. |
| `post_id`, **`source_post_id`**, **`parent_comment_id`**, `thread_id` | Preserve all four. Existing `post_id` means the comment/tweet ID, not the source post. Parent and root thread are different concepts. X retains its conversation-level thread convention. |
| `_platform`, `_source_url`, `_raw_ref`, `_collector_version`, `_collected_at` | Audit provenance and split construction. Platform can be a useful stratification variable, but also a shortcut. Compare per-platform models before using it as a predictor. |
| **`_capture_session`, `_sampling_method`, `_requested_comment_cap`** | Record how an observation entered the sample. Raw browser collection also writes `collection_runs.jsonl`. These are audit fields, not buzzer signals. Legacy runs cannot recover missing provenance. |
| **`_profile_collected_at`, `_profile_status`, `_profile_user_id`** | Distinguish snapshot timing, stale caches, and identity matches. A historical account label must not use a profile snapshot collected after the prediction cutoff. Unknown metadata remains missing. |
| `created_at`, `account_created_at`, `account_age_days` | Keep original timestamps. Age is an optional profile feature; unavailable creation dates are structural missingness. Timing uses UTC. Invalid and negative ages stay null. |
| `text_content`, `clean_text`, `language` | Keep private for annotation and text-model experiments. Language detection is unreliable for short/slang/mixed-language messages. Topic and language can dominate labels; evaluate by topic and language. |
| `hashtags`, `user_mentions`, `urls` | Keep structured values privately, with counts as candidate features. Shared URL/domain and mention-target signals could be useful extensions, but need campaign-diverse data and robust URL normalization. Current data has no useful URL-count variation. |
| `media_types`, `media_count` | Count non-text attachments only. Platform parsers do not yet reliably detect all attachments; exclude from the initial baseline until validated. |
| `source_device`, `user_recent_posts`, `user_recent_timestamps` | Currently unavailable/unpopulated. Keep only as reserved schema fields, never as pretend observed histories or baseline features. |
| `followers_count`, `following_count`, `total_posts_count` | Optional profile ablation. Definitions and visibility differ: subscribers differ from followers, videos from status counts. TikTok and Instagram can expose following counts through enrichment; YouTube has no captured equivalent. Missingness may reveal the platform or scraper success. |
| `follower_to_following_ratio`, **`log_follower_following_ratio`** | Corrected and smoothed. Prefer the log version in a profile experiment, rather than including both ratios and every correlated component without checking validation results. |
| `is_verified`, `has_custom_avatar`, `bio_text`, `bio_length`, `location`, `handle_digit_suffix` | Weak/context-dependent profile signals. Verification has different meanings across platforms, including paid badges. Digit-heavy handles or short biographies do not establish inauthenticity. Avatar and location stay outside the baseline. |
| `like_count`, `reply_count`, `repost_count`, `is_repost` | Keep canonical for audit or platform-specific experiments. These measure exposure/popularity and depend on snapshot time. Received repost count is not the author's own repost rate. Exclude from the first baseline. |
| `duplicate_text_count`, `corpus_duplicate_count`, `distinct_users_same_text` | Retained for audit. Counts include the current observation and depend on sample size; exact matching preserves punctuation/emoji. Corpus counts are platform-scoped. Maxima are redundant and unstable in tiny samples. |
| **`repeated_text_fraction`, `shared_text_fraction`** | Prefer these normalized summaries to three raw maxima. Shared-text flags require at least 12 word characters to reduce generic-reaction matches; this threshold is a provisional heuristic, not validated evidence. |
| `thread_co_*`, `median_thread_arrival_gap` | Secondary diagnostics. Thread granularity differs by platform. Arrival gap measures adjacent observed comments by different authors; it is not a repeat-pair synchrony test. |
| **`post_co_*`** | Better aligned with accounts recurring across posts. Still affected by popular topics and sampled exposure. Co-occurrence alone does not prove coordination. Skipped-group columns describe computational coverage and should not be predictive inputs. |
| `observed_activity_rate` | Sampled comments divided by the observed platform time span, floored at one day; optional explicit `window_days` must be positive. This is an exposure proxy, not a user's true posting rate. Keep out of the first baseline. |
| `n_comments`, **`n_source_posts`**, `n_videos` | Observation support and breadth. `n_videos` is retained only as a compatibility alias; never include both. Both counts reflect the sampling plan. |
| **`median_intercomment_seconds`, `intercomment_burstiness`, `n_valid_timestamps`, `n_timing_intervals`, `observed_span_days`** | Time features plus explicit support counts. One comment supplies no interval; burstiness requires at least three intervals. Treat these as sample behavior, not complete activity history. |
| Mean/max text/entity counts and `dominant_language` | Start with means. Maxima tend to repeat the same information for one-comment accounts and are sample-size sensitive. `dominant_language` remains audit metadata until its predictive use is justified. |

### Proposed feature baseline

`BASELINE_ACCOUNT_FEATURE_COLUMNS` contains 13 candidates:

```text
text_length_mean, hashtag_count_mean, mention_count_mean, url_count_mean,
reply_fraction, repeated_text_fraction, shared_text_fraction,
n_comments, n_source_posts, post_co_partners, post_co_rate,
median_intercomment_seconds, intercomment_burstiness
```

These are starting hypotheses. Remove constant columns **inside the training fold**. Several are constant or unavailable in today's sample. `PROFILE_ACCOUNT_FEATURE_COLUMNS` defines an optional eight-column profile experiment. `ACCOUNT_FEATURE_COLUMNS` remains the wider candidate export for comparison; it is not an instruction to train on every exported column. `feature_quality.csv` reports per-platform missingness and constant/empty columns automatically after assembly.

Do not infer a label from these heuristics and then evaluate a classifier against that same heuristic label. Define the project target explicitly: observable coordinated activity, automation, and paid political advocacy are different claims. Public comments alone generally cannot establish common ownership or payment.

### Data still needed before meaningful selection

Create a separate annotation table keyed by `global_user_id` **and observation window**, with `label`, `label_source`, `label_confidence`, `annotator_id`, `label_timestamp`, `evidence_ref`, and optional independently established `campaign_id`. Keep unknown cases unlabeled; do not force them into the negative class. None of these labels/evidence are invented by this change.

Record `topic_id`/event, `collection_window_start`, `collection_window_end`, sampling order, and target-level collection success in the collection plan. The current notebook exports one historical aggregate per account, not repeated account-window examples. Build explicit window slices before modeling temporal prediction.

Collect multiple relevant posts per platform and multiple independent topics/events, with organic comparison groups and repeated observations of accounts. Breadth is necessary, but there is no defensible universal minimum post/account count without labels, class prevalence, and a power/error analysis. A 500-comment convenience cap is not a representative population sample. Reply traversal can use much of that cap.

If the dataset grows, useful next candidates include same-link reuse across posts, repeated near-synchronous account pairs across posts, and near-duplicate text. The current code does not implement or claim these. Use indexed candidates/time buckets for them, rather than all-pairs comparison of every comment.

### Evaluation and feature selection

1. Choose the prediction cutoff and build only observations available by then. Keep a final held-out topic/event or later period.
2. Keep each account out of both training and validation simultaneously; keep independently known campaign groups together where possible. Account-only splitting does not prevent campaign leakage.
3. Recompute corpus repetition and graph features within the allowed training/inference context of each fold. The notebook's full-corpus exports are for exploration; splitting these afterward is not a leakage-safe model evaluation. A transductive batch-detection setup must be explicitly defined and evaluated as such.
4. Fit missing-value handling, constant-column removal, feature selection, and model tuning within training folds. Compare the behavioral baseline, baseline plus profiles, and platform-specific models. A model's ability to accept nulls does not fix missingness bias.
5. Report precision/recall, PR-AUC, and recall at the operational false-positive tolerance, with per-platform/topic results. Assess correlated features in groups using held-out permutation importance or ablations; do not trust a single training-importance ranking.

These practices follow scikit-learn's documentation on [data leakage and feature selection](https://scikit-learn.org/stable/common_pitfalls.html) and [grouped/time-aware cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html).

## Complexity and validation

Let `P` be total JSON payload size, `N` unique comments, `A` accounts, and `k_g` eligible accounts in group `g`.

| Stage | Cost / limit |
|---|---|
| Coverage | Incremental `O(P)` traversal across newly captured payloads, instead of rescanning all previous payloads at every snapshot; ID-set storage is linear in observed candidates. |
| Raw replay | Streaming `O(P)` JSON parsing plus text normalization/language detection; retains unique canonical records and per-session ID sets rather than all payload JSON objects. A single payload still has to fit in memory. |
| Feature preparation | `O(N log N)` for snapshot/time sorting; fixed-column group/count operations are approximately linear in observations and text size. |
| Coordination | Exact work is inherently `O(sum k_g²)` in dense groups. Eligible accounts need enough groups to qualify; `max_group=1000` and `max_pair_events=2,000,000` bound pair generation **per thread/post calculation**, with explicit missing results when skipped. This is not an exact linear-time graph algorithm. |
| Profiles | One serial browser visit per requested unique uncached/stale handle, `O(A)` visits; disk cache loading is linear in cache rows. Network latency dominates. `max_accounts=0` now correctly means no new visits. |
| Account summaries | `O(N log N)` timing/snapshot ordering plus group aggregation. |

Validation:

- `python -m unittest discover -s tests -v`: 23 offline regression tests passed.
- Every code cell compiles without executing login/collection cells.
- Existing 1,461-row canonical data completed assembly, account aggregation, CSV reload, and pseudonymous exports in temporary directories.
- All 350 local TikTok payloads replayed without errors; 15 previously omitted comments were recovered.
- Synthetic feature/coordination/account pipeline timings on this machine: 2,000 comments **0.141 s**, 4,000 **0.225 s**, 8,000 **0.358 s** with 200 recurring accounts. These are illustrative timings, not performance guarantees; language detection, browser I/O, and rendering are excluded.
- YouTube pagination is tested with a mocked API. Google's documentation explicitly says embedded reply lists [may be incomplete](https://developers.google.com/youtube/v3/docs/commentThreads).

Live TikTok/Instagram/X/Facebook pages were not exercised. Playwright is not installed in the current Python environment, and those flows require real sessions. Instagram GraphQL variants, X layouts, and Facebook payload shapes remain best-effort; synthetic parser tests cannot validate today's live payload contract. X targets should be conversation-root URLs: a target that is itself a reply does not provide enough information here to reconstruct all deeper descendants. Facebook nodes without an identifiable comment type/parent are conservatively ignored.

## Rerun and migration

- Reopen the notebook after file edits. Install dependencies in its kernel environment, then run from the top when ready for new collection.
- Existing browser raw archives still accumulate historical targets; rebuilding canonical files replays that archive, not only the current configuration. Changing target URLs is not a historical-data filter. Filter by source/time explicitly for a modeling cohort.
- Rebuild TikTok canonical rows from raw to use the stable `uid` convention and recover embedded replies. Existing account labels/public hashes keyed to `sec_uid` need a deliberate mapping from raw `user` objects; do not join old and new identity exports blindly.
- Legacy profile-cache rows without a stable profile ID are not trusted for enrichment; a refresh must establish the identity. Old missing capture dates/biography states cannot be retroactively recovered from canonical CSVs alone.
- Public identifiers now use full HMAC hashes and changed domains, so old and new public exports are not compatible. Regenerate both public tables together with the same private salt. Pseudonymization is not anonymity; behavioral fingerprints still exist.
