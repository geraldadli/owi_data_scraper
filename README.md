# Comment scraper

Collect comment text, author names, engagement and publication times with [buzzer.ipynb](buzzer.ipynb).
The notebook includes all **56 video links** from the research spreadsheet:

| Platform | Videos |
| --- | ---: |
| TikTok | 12 |
| X | 11 |
| Instagram | 11 |
| YouTube | 12 |
| Facebook | 10 |

## Setup

```sh
pip install pandas python-dotenv google-api-python-client playwright
playwright install chromium
```

Install these packages in the Python environment used by your notebook kernel.

Use [.env.example](.env.example) as the template for a local `.env` beside the notebook.
If `.env` already exists, add only missing entries; keep your existing key.

```dotenv
YOUTUBE_API_KEY=your_youtube_data_api_key
```

YouTube is skipped if no API key is set.
Unavailable YouTube videos and videos with disabled comments are skipped with a message.
Other API errors stop YouTube collection without replacing its previous CSV; completed
videos remain in the local raw archive. Error tracebacks omit the API request URL.

## Run

1. Open `buzzer.ipynb` in Jupyter or VS Code, with this project folder as the working directory.
2. Edit `VIDEO_URLS` in section 2 if needed. `MAX_COMMENTS` defaults to **500 per video**, including replies.
3. Run the cells from top to bottom. For browser platforms, log in when prompted and press Enter in the notebook to continue.
4. Open `buzzer_data/features/dataset.csv` after collection and export finish.

Browser collection stops when no new comments load, at the batch limit, or at the comment limit.
For TikTok/Instagram, `MAX_COMMENTS` is a stopping threshold: loaded reply threads finish first,
so the CSV can exceed it. Other platforms retain a hard cap.

If comments are blocked while logged out, log in **inside the Chromium window opened by
the notebook**. Your regular browser's login is separate. The collector returns to the video
after the initial login prompt. If no comments are captured, open the comment panel and scroll
manually once, then press Enter in the notebook to retry; type `s` to skip that video.
Scrolling starts after comment data is observed or an Instagram comment panel is detected. Zero captured comments can
also mean an empty/unavailable video or a changed endpoint, not necessarily a login problem.
Sessions persist in `buzzer_data/browser_profile/<platform>/`.

For Instagram, open the video's comment panel before continuing. The collector scrolls
that container directly. If it cannot identify the panel, it stops with an error instead
of scrolling through other Reels or posts.
Instagram captures REST and GraphQL comment responses, including threaded replies.
When nothing has been captured yet, detecting the visible panel starts scrolling to trigger loading.
After login, an already-open target is preserved instead of reloaded and its comment count reset.
At the prompt, Enter retries, `r` reloads the video (reopen its comments afterward), and `s` skips.
Visible comments with no capture do not necessarily mean you need to log in.

### Automatic login

The notebook reuses saved sessions, then tries these `.env` credentials once if needed:

- Instagram: `INSTAGRAM_USERNAME`, `INSTAGRAM_PASSWORD`
- Facebook: `FACEBOOK_EMAIL`, `FACEBOOK_PASSWORD`
- X: `X_USERNAME`, `X_PASSWORD`

Leave credentials blank for manual login. TikTok login remains manual.
If login fails or 2FA, CAPTCHA, consent, or an extra identity check appears, complete it
in the opened browser and press Enter in the notebook. The collector returns to the video.
Credentials are never printed; login responses are excluded from capture during the attempt.
Keep real credentials only in the gitignored `.env`, never in `.env.example` or the notebook.
Restart the kernel after changing credentials (existing environment values take precedence).

## Output

Each exported row represents one comment or reply, with exactly these columns:

Comments containing GIFs, stickers, images, video, audio or other detected attachments are
excluded. A top-level media comment also excludes its entire reply thread. A media reply
excludes only that row. Plain text and emoji remain eligible. No media column is exported.
Text alongside a GIF/sticker/attachment does not make a comment eligible; empty comments
are excluded too. TikTok/Instagram skip opening reply threads whose parent is identified
as media from captured comment metadata, then continue to the next parent.
Filtering uses attachment metadata supplied by the platform; undetected media cannot be filtered.

Reply threads are expanded while scrolling, including Instagram's “Lihat semua 3 balasan”
buttons. TikTok/Instagram finish each opened parent thread before moving on, including delayed
“view more” controls below its loaded replies. They stop with an error if a thread cannot be
isolated/continued or reaches the 1,000-click ceiling, rather than scrolling past it.
New captures retain completed thread replies beyond `MAX_COMMENTS`; old archives keep their original cap.

| Column | Meaning |
| --- | --- |
| `platform` | Source social platform |
| `video_id` | Source video/post ID; Instagram uses its URL shortcode |
| `comment_id` | Platform comment ID, kept as text |
| `parent_comment_id` | Parent comment ID for replies; `\N` for top-level comments |
| `username` | Author handle when supplied; YouTube/Facebook may supply a display name |
| `comment_text` | Original comment or reply text |
| `post_text` | Available source caption/title/description for context |
| `like_count` | Likes on the comment, when available |
| `reply_count` | Replies to the comment, when available |
| `date_published` | Comment publication time in UTC |
| `bias_label` | Initially `\N`; assigned during manual labeling |

Per-platform files: `buzzer_data/canonical/<platform>.csv`.
Combined file: `buzzer_data/features/dataset.csv`.
Missing values use `\N`, not zero. Facebook total reactions are not treated as likes.
Reply counts stay missing when the platform does not supply them; @mentions do not infer counts.
TikTok's reported `reply_count` can cover a whole thread, while `parent_comment_id` links
to the immediate parent (which may itself be a reply). Their counts need not match.
Reply expansion avoids “Hide replies” controls and continues through “View more replies”.
Export reports incomplete TikTok threads after the collection cap and media exclusions;
it never changes platform totals or fabricates missing replies.
Use `(platform, video_id, comment_id)` to identify rows across platforms. IDs are exported as text.
YouTube collects the title and description with one metadata request per collected video.
Browser runs retain the page's Open Graph description; X also uses the original post text
when it appears in the captured response. Descriptions may include platform boilerplate.
TikTok prioritizes the matching video's embedded caption, then its visible caption, before
falling back to Open Graph. It retries before archiving to catch captions loaded after login.
Once captured, that caption also fills missing context on archived comments from the same video.
Unavailable context remains `\N`. Older raw archives may have IDs but no caption metadata.
Rerun parsing/export for IDs; collect again to obtain missing post context.
Keep manually labeled datasets in a separate file: collection/export regenerates the scraper CSVs.
Names come from comment responses; missing names remain null. Display names are not unique account IDs.
Rerun browser parsing and export to restore text/names from existing raw payloads.
Existing three-column CSVs cannot recover those fields themselves; rerun YouTube collection for YouTube.

Account scraping, profile enrichment, behavioral features and modeling have been removed.
Browser login profiles are retained to reuse your sessions. Raw comment responses are
archived locally for replay and can contain inline author information supplied by the platform.
Browser CSVs include previously archived comments. Existing historical account files are untouched.

## Limitations

Video availability and browser response formats can change. Use
`inspect_payloads("instagram")` (or another browser platform name) if parsing fails.
These links are screening candidates; the exported fields do not establish bias
or buzzer activity. The older `BASELINE_SCHEMA.md`, `behavioral_baseline.schema.json` and
`CODE_REVIEW.md` describe the retired account pipeline and are not used by this notebook.

## Offline checks

```sh
python -m unittest discover -s tests -v
```

`.env` and `buzzer_data/` remain gitignored. Offline checks do not log in or scrape live sites.
