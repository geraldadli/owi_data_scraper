# Comment scraper

Collect comment engagement and publication times with [buzzer.ipynb](buzzer.ipynb).
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

Create a `.env` file beside the notebook:

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

Browser collection stops at the comment cap, when no new comments load, or at the batch limit.
The cap is a maximum, not a guaranteed number of comments.

## Output

Each exported row represents one comment or reply, with exactly these columns:

| Column | Meaning |
| --- | --- |
| `like_count` | Likes on the comment, when available |
| `reply_count` | Replies to the comment, when available |
| `date_published` | Comment publication time in UTC |

Per-platform files: `buzzer_data/canonical/<platform>.csv`.
Combined file: `buzzer_data/features/dataset.csv`.
Missing values use `\N`, not zero. Facebook total reactions are not treated as likes.
Comment IDs are used internally for deduplication, but are not exported.

Account scraping, profile enrichment, behavioral features and modeling have been removed.
Browser login profiles are retained to reuse your sessions. Raw comment responses are
archived locally for replay and can contain inline author information supplied by the platform.
Browser CSVs include previously archived comments. Existing historical account files are untouched.

## Limitations

Video availability and browser response formats can change. Use
`inspect_payloads("instagram")` (or another browser platform name) if parsing fails.
These links are screening candidates; the three fields do not establish bias
or buzzer activity. The older `BASELINE_SCHEMA.md`, `behavioral_baseline.schema.json` and
`CODE_REVIEW.md` describe the retired account pipeline and are not used by this notebook.

## Offline checks

```sh
python -m unittest discover -s tests -v
```

`.env` and `buzzer_data/` remain gitignored. Offline checks do not log in or scrape live sites.
