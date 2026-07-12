# NetHome Plus humidity automation — step 1: diagnostics

This first step just confirms what fields your specific AC unit reports
through the cloud, so the real automation (dry-mode-on-high-humidity)
can be written against the real field names instead of guesses.

## Setup

1. Create a new **private** GitHub repository and push these files to it.
2. In the repo, go to **Settings → Secrets and variables → Actions → New
   repository secret** and add two secrets:
   - `MIDEA_ACCOUNT` — your NetHome Plus account email
   - `MIDEA_PASSWORD` — your NetHome Plus account password
3. Go to the **Actions** tab, select **Diagnose NetHome Plus appliance
   fields**, and click **Run workflow**.
4. Once it finishes, open the run and expand the "Run diagnostic script"
   step. Look through the printed attributes for the AC unit for
   anything humidity-related.
5. Share the field name (and its example value) back — that's what the
   real automation script will be built against.

Your credentials only ever live in the repo's encrypted Secrets store;
they aren't printed in the logs.
