# Behavioral baseline schema

The machine-readable contract is [behavioral_baseline.schema.json](behavioral_baseline.schema.json).
Its row grain is **one platform-qualified account in one explicitly defined observation window**.
All values describe sampled behavior, not complete account histories. The schema contains exactly
13 behavioral predictors; identifiers, platform, window boundaries, labels, and missingness reasons
are separate metadata. Existing notebook column names remain supported through the mapping below.

**Implemented in the notebook:** §7 exports the standardized baseline and per-feature statuses;
§7b provides `BaselinePreprocessor` and `make_baseline_model`. §2 exposes UTC observation-window
boundaries and `PLATFORM_FEATURE_MISSINGNESS` for confirmed platform-level gaps. The wide account
table retains legacy aliases, but `BASELINE_ACCOUNT_FEATURE_COLUMNS` now contains the standardized
names. `baseline_accounts.csv` is the dedicated baseline output.

With unset boundaries the CSV is exploratory, with null window metadata; the strict JSONL is empty.
Set both boundaries to produce schema-valid JSONL after filtering comments by event time. Historical
as-of experiments must additionally restrict capture timestamps before feature derivation. The
notebook does not infer collection windows or labels, and does not fit a model automatically.

The notebook also exports three profile audit fields outside the 13 predictors:

| Audit column | Values / interpretation |
|---|---|
| `profile_visibility` | `public`, `private`, or `unknown`; requires an explicit observed privacy flag |
| `profile_access_status` | `readable`, `restricted`, `failed`, or null if no check was recorded |
| `profile_checked_at` | UTC timestamp of that check; null for unchecked/legacy records |

TikTok checks `privateAccount` only after matching profile identity. Instagram's existing successful
profile extractor also reads `is_private`. Unsupported/unvisited profiles remain unknown and unchecked;
no historical privacy status is invented. `restricted` denotes explicitly private account content,
even if basic profile fields were readable. Failed TikTok checks are persisted with unknown visibility,
and attach to comments only through a verified or requested stable account ID. Older statistics survive
a failed recheck only for the same stable ID; their `_profile_collected_at` remains separate from the
new `profile_checked_at`. Account aggregation selects the latest audit check independently of the
statistics snapshot. Public CSVs retain visibility/access status but drop the exact check timestamp.

## The 13 columns

Use `snake_case`, `mean_`/`median_` for summaries, `_count` for counts, `_ratio` for fractions,
and explicit time/length units. Ratios are 0–1, not percentages. Numeric columns are nullable.

| Standardized column | Type / range | Definition | Existing notebook column |
|---|---|---|---|
| `mean_comment_length_chars` | Float ≥ 0 | Mean length of normalized `clean_text`, in Unicode code points | `text_length_mean` |
| `mean_hashtags_per_comment` | Float ≥ 0 | Mean hashtag occurrences per sampled comment | `hashtag_count_mean` |
| `mean_mentions_per_comment` | Float ≥ 0 | Mean mention occurrences per sampled comment | `mention_count_mean` |
| `mean_urls_per_comment` | Float ≥ 0 | Mean URL occurrences per sampled comment | `url_count_mean` |
| `reply_ratio` | Float 0–1 | Replies / comments with known reply status | `reply_fraction` |
| `repeated_comment_ratio` | Float 0–1 | Fraction of comments in the account's repeated, nonempty normalized-text groups | `repeated_text_fraction` |
| `shared_comment_ratio` | Float 0–1 | Fraction matching another account's text in the allowed same-platform corpus, with ≥12 word characters | `shared_text_fraction` |
| `comment_count` | Integer ≥ 1 | Unique sampled comments after deduplication | `n_comments` |
| `source_post_count` | Integer ≥ 1 | Distinct known source posts/videos | `n_source_posts` |
| `recurring_peer_count` | Integer ≥ 0 | Other accounts sharing at least two source posts | `post_co_partners` |
| `max_peer_overlap_ratio` | Float 0–1 | Largest qualifying shared-post count / this account's source-post count | `post_co_rate` |
| `median_comment_interval_seconds` | Float ≥ 0 | Median consecutive comment interval; needs ≥2 valid timestamps | `median_intercomment_seconds` |
| `comment_interval_burstiness` | Float −1–1 | `(s − m)/(s + m)` for intervals, with sample SD `s` and mean `m`; needs ≥3 intervals and a positive denominator | `intercomment_burstiness` |

For repeated comments, `A,A,B` gives `2/3`, not `1/3`: both copies belong to the repeated group.
The overlap ratio is asymmetric and only considers peers sharing at least two posts. It is not
Jaccard similarity and should not be named a probability of coordination.

**Support rules refine the previous export:** an unknown/zero source-post count becomes null;
accounts seen on fewer than two source posts receive null for both peer features, with status
`insufficient_support`. Previous exploratory exports could contain zero in that situation.
When at least two posts are observed and the calculation is complete, zero qualifying peers is
a valid measured zero. Timing zeros from equal timestamps are also valid; missing intervals are not zero.

Text extraction must be usable before text/entity zeros count as observations. The current notebook
can produce zeros from missing text, so renaming columns alone does not establish this contract.
Use canonical text availability and parser diagnostics to mask those cases. If partial coverage is
accepted, retain the eligible-comment denominator/completeness fraction as quality metadata.
`reply_ratio` already uses only known reply statuses; retain that denominator too.

## Platform-specific gaps

These 13 features deliberately exclude profile fields such as following count and account creation
date. Comment behavior is conceptually applicable across the five platforms; that does not mean every
collector currently measures it reliably. Entirely null columns can reflect extraction failures or
insufficient observations rather than a feature having no platform equivalent.

Store `feature_status` per account-window and feature:

| Status | Meaning | Handling |
|---|---|---|
| `observed` | Measured, including genuine zero | Keep value |
| `structural_missing` | The platform/authorized collection method cannot supply the required input | Preserve null; use a shared-feature model or a native-missing model |
| `collection_failed` | Input could exist, but capture/parsing failed | Preserve null; repair/recollect where practical; impute only as an explicit approximation |
| `insufficient_support` | Too few timestamps/posts to estimate the feature | Preserve null; do not invent activity intervals or repeated peers |
| `computation_skipped` | Coordination omitted because of its work budget | Preserve null; track skipped groups; do not encode as zero coordination |
| `unknown` | Legacy data has no defensible availability explanation | Preserve null until investigated |

Do not infer `structural_missing` merely from a 100% null column. Check collector capability and
provenance first. The same feature can have different missingness reasons for different accounts.
Persist a platform × feature report with observed count, null fraction, and reason counts.

Practical examples:

- No usable comment timestamps on a platform: both timing features stay null for its rows.
- One observed comment: interval features are null due to support, regardless of platform.
- Only one source post sampled on a platform: peer features are null due to the collection design.
- Verified text extraction finds no URLs: URL count is zero, not null.
- A future optional profile feature such as YouTube following count has no captured equivalent:
  do not fill it with TikTok/Instagram statistics and claim those are YouTube observations.

## Cross-platform preprocessing and modeling

**Recommended first comparison:** a common-feature logistic-regression baseline versus a pooled
gradient-boosted model that retains nulls. Compare both with and without missingness indicators.

1. **Shared features, conservative baseline.** Within each training fold, keep features with observed
   support on every training platform. Drop features entirely null across training. Optionally require
   a prespecified minimum number of observed accounts per platform; tune this rule inside validation.
   A feature entirely unavailable on one platform is excluded from this model globally. The stored
   schema remains 13 columns, although this model uses a subset.
2. **Native missing-value model.** Keep partially observed features as NaN and use
   `HistGradientBoostingClassifier`. It learns routing for missing values; it does not recover the
   missing measurement. All-null training features carry no learned numerical information and should
   be removed. Avoid relying on this route alone for an entirely unseen platform. [Official documentation](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingClassifier.html)
3. **Imputation when required by the model.** For sporadic gaps, use a training-platform median when
   sufficiently supported. Otherwise, use a pooled **training-only** median only if the feature has
   comparable semantics and units across platforms. Keep a missingness flag. For structural/support
   gaps, prefer options 1 or 2; a pooled median is merely a numeric placeholder for a linear model,
   not inferred behavior. Never round that placeholder into a supposed observed count or save it
   over canonical data.
4. **All-null training column.** Drop it for that fitted model. There is no median to learn. Do not
   estimate one from validation/test rows. If a fixed-width serving interface is mandatory, a constant
   placeholder plus an availability flag may preserve shape, but that column remains uninformative.
   `SimpleImputer(keep_empty_features=True)` retains shape; it does not create evidence. [SimpleImputer documentation](https://scikit-learn.org/stable/modules/generated/sklearn.impute.SimpleImputer.html)
5. **Transforms.** For linear models, apply `log1p` to nonnegative count/mean-count/interval/length
   features, then median-impute and standardize using training parameters. Leave ratios and burstiness
   unlogged; standardize those too. Preserve burstiness's −1–1 range in the stored schema. Tree models
   do not require standardization. Keep optional availability indicators binary.

Platform-conditioned medians are useful only where a platform has observed values. For example,
if training-platform medians for an interval are TikTok=10 s and YouTube=50 s, a missing YouTube
interval can use 50 s. If every Instagram interval is null, there is **no Instagram median**. Choosing
a pooled value then encodes an assumption about cross-platform comparability; test that assumption
against common-feature and native-missing models.

Missingness indicators are **auxiliary inputs**, not additional behavioral features. Thirteen
behavioral columns plus thirteen indicators gives 26 model inputs. Indicators can reveal platform,
collection success, or account sample size, so evaluate their contribution rather than assuming they
are harmless. `MissingIndicator(features="all")` produces a fixed mask for selected features,
including missingness first encountered during inference. The model still cannot learn an effect
for a flag that never varied during training. [MissingIndicator documentation](https://scikit-learn.org/stable/modules/generated/sklearn.impute.MissingIndicator.html)

Avoid KNN/iterative imputation as the default: a platform-wide gap supplies no within-platform targets
with which to validate reconstructed values, and pooled neighbors may primarily reflect platform
differences. Try platform-specific models only after each platform has enough independent labels.
For an unseen platform or severe availability shift, use a validated common-feature fallback or
abstain rather than silently claiming calibrated probabilities.

### Runnable pooled-imputation example

This example operates on **already standardized, availability-masked, fold-specific** feature frames.
It uses a conservative common-feature subset and pooled medians. It does not silently attempt to
reconstruct features entirely missing on one training platform. Put the function inside each outer
training fold; `X_train` must not contain validation/test observations. The native-missing alternative
uses the same selected columns, or a separately evaluated partially observed subset.

```python
import json
import numpy as np
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from sklearn.impute import SimpleImputer, MissingIndicator
from sklearn.linear_model import LogisticRegression

with open("behavioral_baseline.schema.json", encoding="utf-8") as fh:
    props = json.load(fh)["$defs"]["features"]["properties"]

def fit_baseline(X_train, y_train, training_platforms):
    # X_train: the 13 numeric columns in the schema, with genuine np.nan gaps.
    # training_platforms: complete platform Series with the same index as X_train.
    if not training_platforms.index.equals(X_train.index):
        raise ValueError("Platform and feature rows must align")
    if training_platforms.isna().any():
        raise ValueError("Platform metadata is required")
    names = list(props)
    supported = X_train[names].notna().groupby(training_platforms).sum().gt(0).all()
    varying = X_train[names].nunique(dropna=True).gt(1)
    selected = supported.index[supported & varying].tolist()
    if not selected:
        raise ValueError("No supported, varying behavioral features in training")
    log_positions = [i for i, c in enumerate(selected)
                     if props[c]["x-linear-transform"] == "log1p"]

    def transform_values(X):
        values = np.asarray(X, dtype=float).copy()
        values[:, log_positions] = np.log1p(values[:, log_positions])
        return values

    numeric = Pipeline([
        ("log", FunctionTransformer(transform_values)),
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
    ])
    preprocess = FeatureUnion([
        ("values", numeric),
        ("missing", MissingIndicator(features="all")),
    ])
    model = Pipeline([
        ("preprocess", preprocess),
        ("classifier", LogisticRegression(max_iter=2000)),
    ])
    model.fit(X_train[selected], y_train)
    return selected, model

# In each fold:
# selected, model = fit_baseline(X_train, y_train, training_platforms)
# probabilities = model.predict_proba(X_valid[selected])[:, 1]
```

For the native route, replace the preprocessing/classifier above with
`HistGradientBoostingClassifier(random_state=42, early_stopping=False)` and fit selected numeric
columns containing NaNs. Disabling automatic early stopping avoids creating an ungrouped internal
validation split; tune iterations in the outer grouped training procedure. Do not add labels or
identifiers to the numeric frame. The example's nested transform is intended for experimentation;
move it into a named module-level transformer before portable model serialization.

## Using the schema with this project

- Rename columns using each property's `x-notebook-column` annotation. This is a mapping, not proof
  that legacy zeros were measured. Apply the support/availability rules before assigning statuses.
- Use JSON `null`, pandas nullable `Float64`/`Int64`, and NumPy `NaN` for unavailable numeric values.
  Do not store the strings `"null"`/`"NaN"`, infinity, or sentinel numbers such as −999.
- Keep account/window/platform keys out of the 13-feature numeric matrix. Window boundaries must
  come from the collection/modeling plan, not be inferred from each account's first/last comment.
- Validate that every `observed` status has a numeric value, every other status has null, and window
  end exceeds window start. JSON Schema alone does not enforce those cross-field comparisons.
- Recompute text sharing and peer features in each fold's permitted corpus and before its prediction
  cutoff. Renaming or imputing the existing full-corpus export does not eliminate leakage.
- Evaluate held-out accounts/campaigns and later events, report per-platform precision/recall and
  PR-AUC, and stress-test platform-wide missingness. Fit selection, medians, scalers, and any
  calibration strictly on allowed training data. [Leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html)

With today's one-post-per-platform sample, the peer features are unsupported under this schema,
not evidence that every account is uncoordinated. More account/post/time coverage and independent
labels remain necessary; imputation cannot supply them.
