# PPC Bid Changes Sync

Runs every 6 hours via GitHub Actions. Fetches the last 7 days of bid-related changes from Google Ads and writes them to `bid-changes.json`.

The Domain Campaigns Bid Optimizer dashboard reads from this file.

## GitHub Secrets required

Add these in repo Settings → Secrets → Actions:

| Secret | Value |
|--------|-------|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | from ~/.codex/config.toml |
| `GOOGLE_ADS_CLIENT_ID` | from ~/.codex/config.toml |
| `GOOGLE_ADS_CLIENT_SECRET` | from ~/.codex/config.toml |
| `GOOGLE_ADS_REFRESH_TOKEN` | from ~/.codex/config.toml |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | `5156996580` |
