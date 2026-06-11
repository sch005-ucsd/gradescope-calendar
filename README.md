# gradecal

`gradecal` is a command-line tool that reads the due dates of all your
Gradescope assignments and syncs them directly into Google Calendar. Run it once
to backfill your deadlines, or schedule it to run daily so new assignments and
changed due dates appear on your calendar automatically, with no manual steps.

## How it works

`gradecal` logs into Gradescope as you (it has no public API, so it reads the
same pages you see in a browser), collects every dated assignment, and writes
each one as an event in a dedicated "Gradescope" calendar in your Google account.
The sync is *idempotent*: every assignment maps to a stable event, so running it
repeatedly adds new deadlines and updates moved ones without ever creating
duplicates.

## Setup

### 1. Install

```
uv add "git+https://github.com/<your-username>/gradecal.git"
```

### 2. Get a Google OAuth client

`gradecal` talks to your own Google account, so you provide your own OAuth
client (a one-time setup):

1. Go to the [Google Cloud Console](https://console.cloud.google.com/), create a
   project, and enable the **Google Calendar API**.
2. Configure the **OAuth consent screen** as an *External* app. Set its
   publishing status to **In production** — otherwise Google revokes your login
   after 7 days, which would break a daily sync. (Personal-use apps with under
   100 users don't need verification; you just click past an "unverified app"
   notice once.)
3. Create an **OAuth client ID** of type **Desktop app** and download its JSON.
4. Save that file as `~/.config/gradecal/credentials.json`.

### 3. Authorize (one time)

```
gradecal auth
```

This opens a browser, asks you to grant calendar access, and saves a token to
`~/.config/gradecal/token.json`. After this, syncs run without a browser.

### 4. Provide your Gradescope login

`gradecal` reads `GRADESCOPE_EMAIL` and `GRADESCOPE_PASSWORD` from the
environment. If they aren't set, it asks for your email and prompts for your
password securely (so it never lands in your shell history):

```
export GRADESCOPE_EMAIL="you@ucsd.edu"
export GRADESCOPE_PASSWORD="..."
```

## Usage

**Check which courses gradecal can see:**

```
gradecal list
```

**Sync all assignments into Google Calendar:**

```
gradecal sync
```

**Sync only certain courses** (match by code or name, repeatable):

```
gradecal sync -c "DSC 80" -c "DSC 100"
```

**Change the reminder lead time** (minutes before each deadline; `0` disables
reminders). This sets a reminder 2 hours before instead of the default 1 day:

```
gradecal sync -r 120
```

Other options: `--calendar-name` to write to a differently named calendar, and
`--timezone` for any deadline parsed without an explicit timezone. See
`gradecal sync --help`.

## Automating the daily sync

Because `sync` is idempotent, you can run it on a schedule and forget about it.
Two ways:

**Local (cron).** Runs on your own machine; credentials never leave it. The
catch is it only runs while the machine is awake. See `crontab.example` for a
ready-to-edit entry — it sources your Gradescope credentials from a private file
and runs `gradecal sync` every morning.

**Cloud (GitHub Actions).** Runs daily regardless of whether your machine is on.
The workflow in `.github/workflows/sync.yml` does this; you store your Gradescope
credentials and your `token.json` contents as encrypted repository secrets
(`GRADESCOPE_EMAIL`, `GRADESCOPE_PASSWORD`, `GOOGLE_TOKEN_JSON`). The stored token
refreshes its own access tokens on every run.

## Notes

The Gradescope scraping selectors live in `src/gradecal/client.py` (the
`_parse_*` functions); if Gradescope changes its page layout, that's the only
place to update. Accounts that log in only through university SSO are not
supported. `gradecal` only adds and updates events — it never deletes them, so
removing an assignment on Gradescope won't remove it from your calendar.
