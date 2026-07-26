"""
Humidity-priority AC automation with a temperature goal and a daily
maintenance window.

Each cycle:
  1. If the current Pacific-time clock falls inside the maintenance
     window, exit immediately without touching Govee or the AC at all.
  2. Read current humidity and temperature from the Govee sensor, and
     the AC's live state from the Midea cloud.
  3. If humidity > HUMIDITY_TRIGGER: switch to Dry mode (this also
     cools the room), regardless of the current temperature.
  4. Otherwise: if room temp > DESIRED_ROOM_TEMP_F, switch to Auto
     mode with the target temperature set to DESIRED_ROOM_TEMP_F; if
     room temp <= DESIRED_ROOM_TEMP_F, make sure the AC is off.

There is no saved/restored state anymore — every cycle re-decides
fresh from current readings, since Dry mode also cools the room so
there's nothing to preserve across a humidity intervention.

Requires env vars:
  GOVEE_API_KEY      - Govee Developer API key
  MIDEA_ACCOUNT      - NetHome Plus account email
  MIDEA_PASSWORD     - NetHome Plus account password

Optional env vars (defaults shown):
  HUMIDITY_TRIGGER     = 65   (% humidity above which Dry mode runs)
  DESIRED_ROOM_TEMP_F  = 78   (Auto-mode target / off threshold)
  MAX_TEMP_ALERT_F     = 80   (either sensor above this emails an alert)
  MAINTENANCE_START    = ""   (Pacific time, 24h, e.g. "22:00")
  MAINTENANCE_END      = ""   (Pacific time, 24h, e.g. "06:00")
"""

import os
import sys
import uuid
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import requests
from midea_beautiful import appliance_state, connect_to_cloud, find_appliances

# --- Config -----------------------------------------------------------

HUMIDITY_TRIGGER = float(os.environ.get("HUMIDITY_TRIGGER") or "65")
DESIRED_ROOM_TEMP_F = float(os.environ.get("DESIRED_ROOM_TEMP_F") or "78")
MAX_TEMP_ALERT_F = float(os.environ.get("MAX_TEMP_ALERT_F") or "80")

MAINTENANCE_START = os.environ.get("MAINTENANCE_START") or ""
MAINTENANCE_END = os.environ.get("MAINTENANCE_END") or ""
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

GOVEE_SKU = "H5103"
GOVEE_DEVICE = "A0:38:E6:E9:C0:46:12:59"
GOVEE_BASE_URL = "https://openapi.api.govee.com/router/api/v1"

MIDEA_DRY_MODE = 3
MIDEA_AUTO_MODE = 1

MIDEA_MODE_NAMES = {
    1: "Auto",
    2: "Cool",
    3: "Dry",
    4: "Heat",
    5: "Fan",
}


# --- Maintenance window -----------------------------------------------

def in_maintenance_window() -> bool:
    if not MAINTENANCE_START or not MAINTENANCE_END:
        return False
    start = dtime.fromisoformat(MAINTENANCE_START)
    end = dtime.fromisoformat(MAINTENANCE_END)
    now = datetime.now(PACIFIC_TZ).time()
    if start <= end:
        return start <= now < end
    # Window spans midnight (e.g. 22:00 - 06:00)
    return now >= start or now < end


# --- Govee sensor readings ------------------------------------------------

def get_govee_reading(api_key: str) -> tuple[float, float]:
    """Returns (humidity_pct, temperature_f) from the Govee sensor."""
    headers = {"Govee-API-Key": api_key, "Content-Type": "application/json"}
    body = {
        "requestId": str(uuid.uuid4()),
        "payload": {"sku": GOVEE_SKU, "device": GOVEE_DEVICE},
    }
    resp = requests.post(
        f"{GOVEE_BASE_URL}/device/state", headers=headers, json=body, timeout=15
    )
    resp.raise_for_status()
    capabilities = resp.json()["payload"]["capabilities"]

    humidity = None
    temperature_f = None
    for cap in capabilities:
        instance = cap.get("instance")
        if instance == "sensorHumidity":
            humidity = float(cap["state"]["value"])
        elif instance == "sensorTemperature":
            temperature_f = float(cap["state"]["value"])

    if humidity is None:
        raise RuntimeError("sensorHumidity not found in Govee response")
    if temperature_f is None:
        raise RuntimeError("sensorTemperature not found in Govee response")

    return humidity, temperature_f


# --- Midea AC -------------------------------------------------------------

def get_ac(account: str, password: str):
    cloud = connect_to_cloud(account=account, password=password)
    appliances = find_appliances(account=account, password=password)
    ac = next((a for a in appliances if getattr(a, "type", None) == "0xAC"), None)
    if ac is None:
        raise RuntimeError("No air conditioner appliance found on this account.")

    appliance_id = (
        getattr(ac, "id", None)
        or getattr(ac.state, "id", None)
        or getattr(ac.state, "_id", None)
    )
    if appliance_id is None:
        raise RuntimeError("Could not determine appliance id for the AC.")

    refreshed = appliance_state(
        cloud=cloud,
        use_cloud=True,
        appliance_id=appliance_id,
        appliance_type="0xAC",
    )
    return cloud, refreshed


# --- Main -----------------------------------------------------------------

def run_cycle(govee_key: str, midea_account: str, midea_password: str,
              give_auto_priority: bool) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")

    if in_maintenance_window():
        print(f"Inside maintenance window ({MAINTENANCE_START}-{MAINTENANCE_END} "
              f"Pacific) — skipping this cycle entirely.")
        if github_output:
            with open(github_output, "a") as f:
                f.write("maintenance_skipped=true\n")
        return

    humidity, govee_temp_f = get_govee_reading(govee_key)
    cloud, ac = get_ac(midea_account, midea_password)

    print(f"Humidity: {humidity}%")
    print(f"Govee temp: {govee_temp_f}F")
    print(f"AC state: mode={ac.state.mode} running={ac.state.running} "
          f"target={ac.state.target_temperature} fan={ac.state.fan_speed}")
    print(f"Decision mode this cycle: "
          f"{'give Auto priority' if give_auto_priority else 'humidity always wins'}")

    desired_temp_c = (DESIRED_ROOM_TEMP_F - 32) * 5 / 9
    action_taken = "no action"

    already_auto = ac.state.mode == MIDEA_AUTO_MODE and ac.state.running
    already_dry = ac.state.mode == MIDEA_DRY_MODE and ac.state.running
    too_humid = humidity > HUMIDITY_TRIGGER
    too_hot = govee_temp_f > DESIRED_ROOM_TEMP_F

    if give_auto_priority:
        # Give Auto mode a chance to reduce humidity as a side effect of
        # cooling before escalating to Dry mode, to minimize how often
        # Dry mode actually runs.
        if too_hot:
            if already_dry:
                if too_humid:
                    print(f"Still too humid ({humidity}%) while hot — "
                          f"staying in Dry mode.")
                    action_taken = "no action (staying in Dry mode)"
                else:
                    print(f"Humidity now OK ({humidity}%) and still hot — "
                          f"switching from Dry back to Auto mode at "
                          f"{desired_temp_c:.1f}C.")
                    ac.set_state(mode=MIDEA_AUTO_MODE, running=True,
                                 target_temperature=desired_temp_c, cloud=cloud)
                    action_taken = "turned ON (Auto mode, humidity recovered)"
            elif already_auto:
                if too_humid:
                    print(f"Already in Auto mode but humidity {humidity}% > "
                          f"trigger {HUMIDITY_TRIGGER}% — Auto isn't keeping "
                          f"up, escalating to Dry mode.")
                    ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
                    action_taken = "turned ON (Dry mode — Auto wasn't enough)"
                else:
                    print("Already in Auto mode and humidity is fine — "
                          "nothing to do.")
                    action_taken = "no action (already in Auto mode)"
            else:
                print(f"Temp {govee_temp_f}F > desired {DESIRED_ROOM_TEMP_F}F — "
                      f"giving Auto mode a chance first at {desired_temp_c:.1f}C, "
                      f"even though humidity is {humidity}%.")
                ac.set_state(mode=MIDEA_AUTO_MODE, running=True,
                             target_temperature=desired_temp_c, cloud=cloud)
                action_taken = "turned ON (Auto mode — first attempt)"
        else:
            if too_humid:
                if already_dry:
                    action_taken = "no action (already in Dry mode)"
                else:
                    print(f"Temp is fine but humidity {humidity}% > trigger "
                          f"{HUMIDITY_TRIGGER}% — switching to Dry mode.")
                    ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
                    action_taken = "turned ON (Dry mode — humidity high)"
            elif ac.state.running:
                print("Temp and humidity are both within range — turning "
                      "AC off.")
                ac.set_state(running=False, cloud=cloud)
                action_taken = "turned OFF (temp and humidity within range)"
            else:
                action_taken = "no action (already off)"

    else:
        # Humidity always wins outright.
        if too_humid:
            if already_dry:
                print(f"Humidity {humidity}% > trigger {HUMIDITY_TRIGGER}% — "
                      f"already in Dry mode, nothing to do.")
                action_taken = "no action (already in Dry mode)"
            else:
                print(f"Humidity {humidity}% > trigger {HUMIDITY_TRIGGER}% — "
                      f"switching to Dry mode regardless of temperature.")
                ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
                action_taken = "turned ON (Dry mode — humidity high)"

        elif too_hot:
            if already_auto:
                print(f"Temp {govee_temp_f}F > desired {DESIRED_ROOM_TEMP_F}F — "
                      f"already in Auto mode, nothing to do.")
                action_taken = "no action (already in Auto mode)"
            else:
                print(f"Temp {govee_temp_f}F > desired {DESIRED_ROOM_TEMP_F}F — "
                      f"switching to Auto mode at {desired_temp_c:.1f}C.")
                ac.set_state(mode=MIDEA_AUTO_MODE, running=True,
                             target_temperature=desired_temp_c, cloud=cloud)
                action_taken = "turned ON (Auto mode)"

        else:
            if ac.state.running:
                print(f"Temp {govee_temp_f}F <= desired {DESIRED_ROOM_TEMP_F}F "
                      f"and humidity {humidity}% <= trigger {HUMIDITY_TRIGGER}% "
                      f"— turning AC off.")
                ac.set_state(running=False, cloud=cloud)
                action_taken = "turned OFF (temp and humidity within range)"
            else:
                print("Temp and humidity are within range, and the AC is "
                      "already off — nothing to do.")
                action_taken = "no action (already off)"

    mode_name = MIDEA_MODE_NAMES.get(ac.state.mode, "Unknown")
    target_f = ac.state.target_temperature * 9 / 5 + 32
    ac_indoor_f = ac.state.indoor_temperature * 9 / 5 + 32

    high_temp_alert = govee_temp_f > MAX_TEMP_ALERT_F or ac_indoor_f > MAX_TEMP_ALERT_F
    alert_sources = []
    if govee_temp_f > MAX_TEMP_ALERT_F:
        alert_sources.append(f"Govee sensor: {govee_temp_f:.1f}F")
    if ac_indoor_f > MAX_TEMP_ALERT_F:
        alert_sources.append(f"AC unit: {ac_indoor_f:.1f}F")
    alert_message = "; ".join(alert_sources) if alert_sources else "none"

    if github_output:
        with open(github_output, "a") as f:
            f.write("maintenance_skipped=false\n")
            f.write(f"humidity={humidity}\n")
            f.write(f"govee_temp_f={govee_temp_f:.1f}\n")
            f.write(f"mode={ac.state.mode}\n")
            f.write(f"mode_name={mode_name}\n")
            f.write(f"running={ac.state.running}\n")
            f.write(f"target={ac.state.target_temperature}\n")
            f.write(f"target_f={target_f:.1f}\n")
            f.write(f"ac_indoor_f={ac_indoor_f:.1f}\n")
            f.write(f"fan={ac.state.fan_speed}\n")
            f.write(f"humidity_trigger={HUMIDITY_TRIGGER}\n")
            f.write(f"desired_temp_f={DESIRED_ROOM_TEMP_F}\n")
            f.write(f"action={action_taken}\n")
            f.write(f"action_occurred={'true' if not action_taken.startswith('no action') else 'false'}\n")
            f.write(f"high_temp_alert={'true' if high_temp_alert else 'false'}\n")
            f.write(f"alert_message={alert_message}\n")


def main() -> int:
    govee_key = os.environ.get("GOVEE_API_KEY")
    midea_account = os.environ.get("MIDEA_ACCOUNT")
    midea_password = os.environ.get("MIDEA_PASSWORD")

    if not all([govee_key, midea_account, midea_password]):
        print("Missing one or more required env vars "
              "(GOVEE_API_KEY, MIDEA_ACCOUNT, MIDEA_PASSWORD).")
        return 1

    give_auto_priority = (os.environ.get("GIVE_AUTO_PRIORITY") or "true").lower() == "true"
    run_cycle(govee_key, midea_account, midea_password, give_auto_priority)
    return 0


if __name__ == "__main__":
    sys.exit(main())
