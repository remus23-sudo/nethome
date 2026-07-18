NETHOME PLUS / MIDEA AC HUMIDITY & TEMPERATURE AUTOMATION
============================================================

WHAT THIS DOES
--------------
This automation watches humidity and temperature in a room using a
Govee WiFi thermo-hygrometer, and automatically switches a Midea /
NetHome Plus air conditioner into Dry mode when humidity gets too
high, restoring whatever the AC was doing before once humidity drops
back down. It also sends a high-temperature alert email if either the
Govee sensor or the AC's own sensor reads above a configurable limit.

Everything runs on GitHub Actions - there is no home server, Raspberry
Pi, or always-on device required. Both the AC and the Govee sensor are
controlled/read entirely through their cloud APIs.


FILES
-----
automate.py
    The main script. Reads humidity/temperature from Govee, reads and
    controls the AC via the Midea cloud, decides what action (if any)
    to take, and writes outputs used by the email steps.

requirements.txt
    Python dependency (midea-beautiful-air) for automate.py.

state.json
    Created automatically by automate.py when it switches the AC into
    Dry mode. Stores the AC's mode/running/target temperature/fan
    speed from just before the switch, so it can be restored later.
    Committed back to the repo by the workflow so it survives between
    runs. Deleted automatically once the AC is restored. If this file
    is ever stuck (e.g. after manual testing), you can delete it by
    hand from the GitHub web UI to reset the automation to a clean
    state.

.github/workflows/humidity-automation.yml
    The GitHub Actions workflow. Installs dependencies, runs
    automate.py, commits state.json if it changed, and sends email
    notifications.

diagnose.py / govee_diagnose.py
    One-off diagnostic scripts used while building this automation, to
    inspect the raw Midea and Govee API responses. Not part of the
    regular automation; safe to ignore or delete.


HOW THE AUTOMATION LOGIC WORKS
-------------------------------
Each run:
  1. Reads current humidity and temperature from the Govee sensor.
  2. Reads the AC's live state from the Midea cloud.
  3. Picks the humidity thresholds to use this run:
       - Normal thresholds: HUMIDITY_TRIGGER / HUMIDITY_RESET
       - If the Govee temperature reading is below 72F, it instead
         uses a fixed "cold room" pair (trigger=65, reset=60), since
         a cold room needs a different humidity comfort band.
  4. If humidity is above the trigger and there's no saved prior
     state yet: saves the AC's current mode/running/target
     temperature/fan speed to state.json, then switches it to Dry
     mode.
  5. If humidity is still above the trigger, a saved state already
     exists, but the AC isn't actually running Dry mode (e.g. someone
     turned it off manually) - it re-asserts Dry mode without
     overwriting the originally saved snapshot.
  6. If humidity drops below the reset threshold and a saved state
     exists: restores the AC to that saved mode/running/target
     temperature/fan speed, then deletes state.json.
  7. Otherwise: does nothing.
  8. Separately, checks both sensors against MAX_TEMP_ALERT_F. If
     either is above it, sends a high-temperature alert email
     regardless of the EMAIL_NOTIFICATIONS setting below.


REQUIRED SECRETS
-----------------
Set these under: repo Settings -> Secrets and variables -> Actions ->
Secrets tab. Secrets are hidden after saving (you can only overwrite,
not view them).

  GOVEE_API_KEY
      Govee Developer API key. Get it in the Govee Home app:
      Settings -> About Us -> Apply for API Key.

  MIDEA_ACCOUNT
      Your NetHome Plus account email.

  MIDEA_PASSWORD
      Your NetHome Plus account password.

  MAIL_USERNAME
      The Gmail address emails are sent from and to.

  MAIL_PASSWORD
      A Gmail App Password (not your normal Gmail password). Requires
      2-Step Verification to be enabled on the Google account. Create
      one at: Google Account -> Security -> 2-Step Verification ->
      App passwords.


REQUIRED / OPTIONAL VARIABLES
-------------------------------
Set these under: repo Settings -> Secrets and variables -> Actions ->
Variables tab. Unlike secrets, variables stay visible so you can check
their current value at any time.

  HUMIDITY_TRIGGER
      Humidity percent above which the AC switches to Dry mode, under
      normal (72F+) conditions. Example: 60
      Default if unset: 65

  HUMIDITY_RESET
      Humidity percent below which the AC is restored to its prior
      state, under normal conditions. Must be lower than
      HUMIDITY_TRIGGER, with enough of a gap (5-10+ points) to avoid
      the AC flipping on and off repeatedly right at the boundary.
      Example: 52
      Default if unset: 55

  MAX_TEMP_ALERT_F
      Temperature in Fahrenheit above which a high-temperature alert
      email is sent, checked against both the Govee sensor and the
      AC's own sensor. This email always sends regardless of the
      EMAIL_NOTIFICATIONS setting.
      Example: 80
      Default if unset: 80

  EMAIL_NOTIFICATIONS
      Controls the regular status email (not the high-temp alert,
      which always sends). One of:
        All    - email on every run, action taken or not
        Action - email only on runs where the AC was actually
                 switched (turned on to Dry, or restored)
        None   - no regular status emails (or leave unset / any other
                 value)


NOTES / KNOWN LIMITATIONS
---------------------------
- The AC's own indoor_temperature reading has historically
  under-reported real room temperature (likely due to sensor
  placement near the internal coils), so the Govee reading should be
  treated as the more trustworthy one for room temperature.

- Midea AC mode numbers, as far as confirmed on this specific unit:
    1 = Auto, 2 = Cool, 3 = Dry, 4 = Heat, 5 = Fan
  A mode value of 6 has been seen once and is not yet mapped/
  confirmed - if you see "Unknown" as a mode name in an email, check
  the NetHome Plus app directly to see what it's actually showing.

- GitHub's own `schedule:` (cron) trigger for Actions can be
  unreliable, especially at short intervals like every 15 minutes, on
  free-tier repos - GitHub explicitly documents scheduled workflows as
  best-effort and deprioritized on the free tier. If this workflow
  isn't firing on time, an external cron service (e.g. cron-job.org)
  calling GitHub's API to trigger workflow_dispatch is a more reliable
  workaround than relying on GitHub's built-in schedule.

- Both the Midea and Govee cloud integrations were built by directly
  inspecting live API/library responses rather than trusting
  documentation, since the installed midea-beautiful-air library's
  actual behavior didn't match its own published docs in several
  places. If either the Midea or Govee library/API changes in the
  future and something stops working, re-running the diagnostic
  scripts (diagnose.py, govee_diagnose.py) is the fastest way to see
  what changed.
