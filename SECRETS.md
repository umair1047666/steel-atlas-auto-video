# GitHub Secrets — what the user enters himself

**Nothing in this repo contains a real key.** Every credential lives in the
repo's GitHub Secrets. Path (GitHub web):
**Repo → Settings → Secrets and variables → Actions → New repository secret.**

| Secret name | What it is | Where the user gets it |
|---|---|---|
| `PEXELS_API_KEY` | Pexels API key (primary stock footage; 200 req/hr, 20,000/mo per key). User already holds 3 keys. | pexels.com/api — user pastes one of his existing keys |
| `PIXABAY_API_KEY` | Pixabay API key (backup stock footage; 100 req/min). Free, still issuing keys. | pixabay.com/api/docs/ — user signs up free and pastes the key |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token for Workers AI (FLUX.1 schnell stills). Free tier: 10,000 Neurons/day. Needs **Workers AI: Read** permission. | Cloudflare dashboard → My Profile → API Tokens → Create Token → Workers AI template |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID (used with the token above). | Cloudflare dashboard → right sidebar, Account ID |
| `DRIVE_CLIENT_ID` | Google OAuth client ID for Drive upload (**user OAuth, NOT a service account** — a service account only gets 15 GB; the user's Drive is 5 TB). | Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID (Desktop app). App must be published to **Production** or the refresh token dies after 7 days. |
| `DRIVE_CLIENT_SECRET` | Google OAuth client secret (same client as above). | Same place as above |
| `DRIVE_REFRESH_TOKEN` | OAuth refresh token for the user's own Google account (Drive scope). | Generated once with the OAuth client above (e.g. via the Google OAuth Playground, or the existing scheduler's flow). Never pasted in chat. |
| `DRIVE_FOLDER_ID` | Drive folder where finished videos are uploaded. | Open the folder in Drive; the ID is the last part of its URL |
| `DRIVE_MUSIC_FILE_ID` | Drive file ID of the royalty-free music track (`track.mp3`). The render stage downloads it from Drive — music is never committed to git. | Upload `track.mp3` to Drive (web); the file ID is the last part of its share URL |
| `DRIVE_JOBS_FOLDER_ID` | (optional) Drive folder for the between-stage job store. Defaults to a `pipeline-jobs` subfolder inside `DRIVE_FOLDER_ID`. | Same as `DRIVE_FOLDER_ID` unless the user wants a separate folder |
| `YT_CLIENT_ID` | Google OAuth client ID for the Steel Atlas YouTube channel. | Same Google Cloud project as Drive, or the existing scheduler's per-slot client (YT_1…YT_10 pattern) |
| `YT_CLIENT_SECRET` | Google OAuth client secret for the YouTube channel. | Same place as above |
| `YT_REFRESH_TOKEN` | OAuth refresh token for the YouTube channel (youtube.upload scope). | Generated once with the OAuth client above |
| `YOUTUBE_PRIVACY` | (optional) `private` (default), `unlisted` or `public`. | User's choice; leave unset for `private` |

**No key is needed for:** NASA Images API, Wikimedia Commons, Openverse,
Edge TTS, Piper (voice model downloads are public files).

**Rotation rule:** if any key ever appears in chat, a screenshot or a log,
rotate it immediately — secrets in chat are transient only.
