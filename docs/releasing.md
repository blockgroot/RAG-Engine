# Releasing a Handbook Version

How we ship a version to Cloud and to every self-hosted install, and how their admins hear about it.

## Once, before the first release

1. **The public release list.** `blockgroot/handbook-releases` holds `releases.json` (what every install checks) and `notes/<version>.md`. Give it a first commit with `releases.json` containing `{"releases": []}`.
2. **The secret.** Create a fine-grained GitHub token with **Contents: read and write** on `blockgroot/handbook-releases` only, and add it to this repo's Actions secrets as `RELEASES_REPO_TOKEN`.
3. **Cloud.** On Render, set `HANDBOOK_DEPLOYMENT=cloud` (the default) and `HANDBOOK_VERSION` to the version Cloud runs.

## Each release

1. **Write the notes** in `release-notes/<version>.md` and merge them:
   ```
   security: false
   manual_steps: false

   - Charts from uploaded spreadsheets
   - Faster Slack replies
   ```
   Each `- ` line is shown to admins in the app; write for them, not for us. Set `manual_steps: true` when an upgrade needs a person: new `.env` settings, new connector app scopes, or a changed install package (`docker-compose.yml`, `Caddyfile`, scripts). No install offers the Update button across such a release. Set `security: true` for a security fix: it is shown as urgent.
2. **Deploy Cloud** from the commit you are about to tag, and set `HANDBOOK_VERSION` on Render to the new version.
3. **Tag:** `git tag v<version> && git push origin v<version>`. The workflow (`.github/workflows/release.yml`):
   1. stops at once if the notes file is missing or malformed;
   2. builds and pushes `handbook-api`, `handbook-web` and `handbook-updater`;
   3. creates the GitHub release here, with the install package;
   4. publishes the install package as a release in the public repo (the link companies download from), then adds the version to its `releases.json`.
4. **Email** the release notes to self-hosted customers.

## What admins see

Within about six hours of step 3:

| | Cloud | Self-hosted |
| --- | --- | --- |
| Bell and Admin › Updates | "Handbook was updated to X", with the notes, for a week | "Handbook X is available", with the notes |
| Button | None: we already updated it | **Update** (or the `./upgrade.sh` command when the release needs manual steps or the company turned the updater off) |

Small fixes can still be deployed to Cloud without a tag; admins are not told about them.
