# NetHome Plus / Midea AC Humidity & Temperature Automation

## What this does

This automation watches humidity and temperature in a room using a Govee WiFi thermo-hygrometer, and controls a Midea / NetHome Plus air conditioner with a two-phase strategy across its three cycles per run, aimed at minimizing how often Dry mode actually needs to run:

- **Cycles 1 and 2 (give Auto mode a chance first)**: if the room is hot, it tries **Auto mode** first — even if humidity is also high — since cooling naturally reduces humidity too. It only escalates to **Dry mode** if the AC is already running Auto and humidity is still above trigger despite that.
- **Cycle 3, 20 minutes after cycle 1 (humidity always wins outright)**: a simpler, stricter pass — if humidity is above trigger, switch to Dry mode regardless of temperature, no exceptions.

There is no saved/restored state — every cycle looks at current readings (and the AC's current live mode) and decides what it should be doing right now, rather than remembering and restoring a snapshot from before. A daily **maintenance window** (Pacific Time) can also be set, during which the automation skips entirely and touches nothing.

Everything runs on **GitHub Actions** — no home server, Raspberry Pi, or always-on device required. Both the AC and the Govee sensor are controlled/read entirely through their cloud APIs.

## Files

| File | Purpose |
|---|---|
| `automate.py` | The main script. Reads humidity/temperature from Govee, reads and controls the AC via the Midea cloud, decides what action (if any) to take, and writes outputs used by the email steps. |
| `requirements.txt` | Python dependencies (`midea-beautiful-air`, `tzdata`) for `automate.py`. |
| `.github/workflows/humidity-automation.yml` | The GitHub Actions workflow. Runs `automate.py` three times per trigger (10 minutes apart each), emailing after each cycle. This should be the only file inside `.github/workflows/` — GitHub sometimes auto-creates a `blank.yml` starter template, and copies of `diagnose.py`/`requirements.txt` or old `diagnose.yml`/`govee-diagnose.yml` workflows can end up there by accident; none of those belong in that folder. |
| `diagnose.py` / `govee_diagnose.py` | One-off diagnostic scripts used while building this automation, to inspect the raw Midea and Govee API responses. Not part of the regular automation; safe to ignore or delete. |

## How the automation logic works

Each run does three cycles, 10 minutes apart, and each cycle first does:

1. **Maintenance window check** — if the current Pacific-time clock falls inside `MAINTENANCE_START`–`MAINTENANCE_END`, the cycle exits immediately without contacting Govee or the AC at all.
2. Reads current humidity and temperature from the Govee sensor, and the AC's live state from the Midea cloud.

Then the cycles differ in decision logic:

### Cycles 1 and 2 — give Auto mode a chance first

- **Room is hot** (temp > `DESIRED_ROOM_TEMP_F`):
  - Not currently running Auto or Dry → switches to **Auto mode** at `DESIRED_ROOM_TEMP_F`, even if humidity is also above trigger right now.
  - Already running Auto, but humidity is still above `HUMIDITY_TRIGGER` → **escalates to Dry mode** (Auto isn't keeping up).
  - Already running Dry, but humidity has recovered to at/below trigger → switches **back to Auto mode**.
- **Room is not hot**:
  - Humidity above trigger → Dry mode (no reason to hold off if temp's already fine).
  - Otherwise → AC off.

### Cycle 3 — humidity always wins outright

A simpler, stricter pass with no exceptions:
- Humidity above `HUMIDITY_TRIGGER` → Dry mode, regardless of temperature.
- Otherwise, temp above `DESIRED_ROOM_TEMP_F` → Auto mode at `DESIRED_ROOM_TEMP_F`.
- Otherwise → AC off.

This means cycles 1 and 2 may deliberately leave the AC in Auto mode even while humidity is technically above trigger (to give it a chance), but cycle 3, 20 minutes after cycle 1 started, will not extend that same patience — if humidity is still high at that point, it switches to Dry mode unconditionally.

Which behavior each cycle uses is controlled by a `GIVE_AUTO_PRIORITY` environment variable set directly in the workflow file (`"true"` for cycles 1 and 2, `"false"` for cycle 3) — this isn't a repo variable you need to configure; it's baked into `.github/workflows/humidity-automation.yml`.

**Separately**, every cycle also checks both sensors (Govee and the AC's own) against `MAX_TEMP_ALERT_F`. If either is above it, that cycle's email gets a high-temperature alert regardless of the `EMAIL_NOTIFICATIONS` setting.

## Required secrets

Set these under: repo **Settings → Secrets and variables → Actions → Secrets tab**. Secrets are hidden after saving (you can only overwrite, not view them).

| Secret | Description |
|---|---|
| `GOVEE_API_KEY` | Govee Developer API key. Get it in the Govee Home app: Settings → About Us → Apply for API Key. |
| `GOVEE_SKU` | Your Govee sensor's model SKU (e.g. `H5103`). Found in the raw device list response from Govee's API, or via the model number on the device/app. |
| `GOVEE_DEVICE` | Your Govee sensor's device MAC address, as returned by Govee's device list API. Identifies exactly which sensor to read. |
| `MIDEA_ACCOUNT` | Your NetHome Plus account email. |
| `MIDEA_PASSWORD` | Your NetHome Plus account password. |
| `MAIL_USERNAME` | The Gmail address emails are sent from and to. |
| `MAIL_PASSWORD` | A Gmail **App Password** (not your normal Gmail password). Requires 2-Step Verification on the Google account. Create one at: Google Account → Security → 2-Step Verification → App passwords. |

## Required / optional variables

Set these under: repo **Settings → Secrets and variables → Actions → Variables tab**. Unlike secrets, variables stay visible so you can check their current value at any time.

| Variable | Description | Example | Default if unset |
|---|---|---|---|
| `HUMIDITY_TRIGGER` | Humidity % above which the AC switches to Dry mode, overriding everything else. | `65` | `65` |
| `DESIRED_ROOM_TEMP_F` | Target room temperature (°F). Above this → Auto mode at this temp (converted to Celsius internally when set on the AC, since that's what the AC's field actually stores); at/below this → AC off (when humidity isn't also triggering Dry mode). | `78` | `78` |
| `MAX_TEMP_ALERT_F` | Temperature (°F) above which a high-temperature alert is folded into that cycle's email, checked against both the Govee sensor and the AC's own sensor. Always included regardless of `EMAIL_NOTIFICATIONS`. | `80` | `80` |
| `MAINTENANCE_START` | Start of the daily maintenance window, Pacific Time, 24-hour format. Leave both this and `MAINTENANCE_END` unset to disable the window entirely. | `22:00` | none (disabled) |
| `MAINTENANCE_END` | End of the daily maintenance window, Pacific Time, 24-hour format. Can be earlier than `MAINTENANCE_START` (e.g. `22:00`–`06:00`) to span midnight. | `06:00` | none (disabled) |
| `EMAIL_NOTIFICATIONS` | Controls the regular status email (the high-temp alert and maintenance-skip notice have their own rules, noted above/below). One of: `All` — email on every run, action or not, and also sends the maintenance-skip notice; `Action` — email only on cycles where the AC was actually switched; `None` — no regular status emails (or leave unset / any other value). | `Action` | none |

## Notes / known limitations

- The AC's own `indoor_temperature` reading has historically under-reported real room temperature (likely due to sensor placement near the internal coils) — the Govee reading is what the automation's decisions are based on for this reason.
- Midea AC mode numbers, as far as confirmed on this specific unit: `1` = Auto, `2` = Cool, `3` = Dry, `4` = Heat, `5` = Fan. A mode value of `6` has been seen once and is not yet mapped/confirmed.
- The maintenance window is checked fresh at the start of each of the three cycles (not once for the whole run), using Pacific Time via Python's `zoneinfo`, which automatically accounts for daylight saving — the same `MAINTENANCE_START`/`END` values mean "10pm local" year-round without any manual adjustment.
- Total run time is now roughly 20+ minutes (three cycles with a 10-minute pause between each), so make sure whatever triggers this workflow (e.g. an external cron service) leaves enough room for one run to finish before the next starts, to avoid overlapping runs.
- Nothing in `automate.py` or the workflow file is hardcoded — every credential and identifier (API keys, account email/password, and even the Govee device's SKU/MAC address) comes from GitHub Secrets, referenced by name. This matters if you're considering making the repo public: the *logic* becomes visible to anyone, but no actual credential or device-identifying value does.
- Both the Midea and Govee cloud integrations were built by directly inspecting live API/library responses rather than trusting documentation, since the installed `midea-beautiful-air` library's actual behavior didn't match its own published docs in several places. If either the Midea or Govee library/API changes in the future and something stops working, re-running the diagnostic scripts (`diagnose.py`, `govee_diagnose.py`) is the fastest way to see what changed.
