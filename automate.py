"""
Humidity-triggered AC automation.

Each run:
  1. Reads current humidity from the Govee WiFi thermo-hygrometer (H5103).
  2. Reads the AC's current live state from the Midea/NetHome Plus cloud.
  3. If humidity > HUMIDITY_TRIGGER and the AC isn't already in an
     override we started: save its current mode/running/target
     temperature/fan speed to state.json, then switch it to dry mode.
  4. If humidity < HUMIDITY_RESET and state.json shows we're in an
     override: restore the saved mode/running/target temperature/fan
     speed, then delete state.json.
  5. Otherwise: do nothing.

state.json is committed back to the repo by the GitHub Actions workflow
so the "prior state" survives between scheduled runs.

Requires env vars:
  GOVEE_API_KEY   - Govee Developer API key
  MIDEA_ACCOUNT   - NetHome Plus account email
  MIDEA_PASSWORD  - NetHome Plus account password
"""

import json
import os
import sys
import uuid
from pathlib import Path

import requests
from midea_beautiful import appliance_state, connect_to_cloud, find_appliances

# --- Config -----------------------------------------------------------

HUMIDITY_TRIGGER = float(os.environ.get("HUMIDITY_TRIGGER") or "65")
HUMIDITY_RESET = float(os.environ.get("HUMIDITY_RESET") or "55")

COLD_ROOM_TEMP_F = 72.0
COLD_ROOM_TRIGGER = 65.0
COLD_ROOM_RESET = 60.0

MAX_TEMP_ALERT_F = float(os.environ.get("MAX_TEMP_ALERT_F") or "80")

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

DESIRED_ROOM_TEMP_F = float(os.environ.get("DESIRED_ROOM_TEMP_F") or "78")

STATE_FILE = Path("state.json")
TEMP_STATE_FILE = Path("temp_state.json")


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


def current_snapshot(ac) -> dict:
    return {
        "mode": ac.state.mode,
        "running": ac.state.running,
        "target_temperature": ac.state.target_temperature,
        "fan_speed": ac.state.fan_speed,
    }


# --- State file (saved prior AC state) -------------------------------------

def load_saved_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(snapshot: dict) -> None:
    STATE_FILE.write_text(json.dumps(snapshot, indent=2))


def clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def load_temp_saved_state():
    if TEMP_STATE_FILE.exists():
        return json.loads(TEMP_STATE_FILE.read_text())
    return None


def save_temp_state(snapshot: dict) -> None:
    TEMP_STATE_FILE.write_text(json.dumps(snapshot, indent=2))


def clear_temp_state() -> None:
    if TEMP_STATE_FILE.exists():
        TEMP_STATE_FILE.unlink()


# --- Main -----------------------------------------------------------------

def run_cycle(govee_key: str, midea_account: str, midea_password: str) -> None:
    humidity, govee_temp_f = get_govee_reading(govee_key)
    cloud, ac = get_ac(midea_account, midea_password)

    if govee_temp_f < COLD_ROOM_TEMP_F:
        effective_trigger = COLD_ROOM_TRIGGER
        effective_reset = COLD_ROOM_RESET
        print(f"Room temp {govee_temp_f}F is below {COLD_ROOM_TEMP_F}F — using "
              f"cold-room thresholds: trigger={effective_trigger} "
              f"reset={effective_reset}")
    else:
        effective_trigger = HUMIDITY_TRIGGER
        effective_reset = HUMIDITY_RESET

    print(f"Humidity: {humidity}%")
    print(f"AC state: mode={ac.state.mode} running={ac.state.running} "
          f"target={ac.state.target_temperature} fan={ac.state.fan_speed}")

    # --- Independent temperature-based Auto mode routine (runs first) ----
    temp_saved = load_temp_saved_state()
    ac_in_auto = ac.state.mode == MIDEA_AUTO_MODE and ac.state.running
    temp_reset_f = DESIRED_ROOM_TEMP_F
    desired_temp_c = (DESIRED_ROOM_TEMP_F - 32) * 5 / 9
    temp_action_taken = "no action"

    if govee_temp_f >= DESIRED_ROOM_TEMP_F and temp_saved is None:
        temp_snapshot = current_snapshot(ac)
        save_temp_state(temp_snapshot)
        print(f"Room temp {govee_temp_f}F >= desired {DESIRED_ROOM_TEMP_F}F — "
              f"saving state {temp_snapshot} and switching to Auto mode at "
              f"{desired_temp_c:.1f}C.")
        ac.set_state(mode=MIDEA_AUTO_MODE, running=True,
                     target_temperature=desired_temp_c, cloud=cloud)
        temp_action_taken = "turned ON (switched to Auto mode)"

    elif govee_temp_f >= DESIRED_ROOM_TEMP_F and temp_saved is not None and not ac_in_auto:
        print(f"Room temp still >= desired {DESIRED_ROOM_TEMP_F}F and an "
              f"override is recorded, but the AC isn't actually in Auto mode "
              f"(mode={ac.state.mode} running={ac.state.running}) — "
              f"re-asserting Auto mode at {desired_temp_c:.1f}C without "
              f"touching the saved state.")
        ac.set_state(mode=MIDEA_AUTO_MODE, running=True,
                     target_temperature=desired_temp_c, cloud=cloud)
        temp_action_taken = "turned ON (re-asserted Auto mode)"

    elif govee_temp_f < temp_reset_f and temp_saved is not None:
        print(f"Room temp below {temp_reset_f}F — restoring saved state "
              f"{temp_saved}.")
        if temp_saved["mode"] == MIDEA_AUTO_MODE:
            print("Saved state was itself Auto mode — turning off instead "
                  "of restoring Auto mode.")
            ac.set_state(running=False, cloud=cloud)
            temp_action_taken = "turned OFF (saved state was Auto mode)"
        else:
            ac.set_state(
                mode=temp_saved["mode"],
                running=temp_saved["running"],
                target_temperature=temp_saved["target_temperature"],
                fan_speed=temp_saved["fan_speed"],
                cloud=cloud,
            )
            restored_mode_name = MIDEA_MODE_NAMES.get(temp_saved["mode"], "Unknown")
            temp_action_taken = (
                f"restored to ON ({restored_mode_name})" if temp_saved["running"]
                else f"turned OFF (restored, was {restored_mode_name})"
            )
        clear_temp_state()

    else:
        print("No temperature-based action needed this run.")

    # --- Independent comfort-off check: room is both cool and dry --------
    comfort_off_action = "no action"
    if govee_temp_f < DESIRED_ROOM_TEMP_F and humidity < effective_reset:
        print(f"Room temp {govee_temp_f}F is below desired {DESIRED_ROOM_TEMP_F}F "
              f"and humidity {humidity}% is below reset {effective_reset}% — "
              f"turning AC off.")
        ac.set_state(running=False, cloud=cloud)
        if STATE_FILE.exists():
            clear_state()
        if TEMP_STATE_FILE.exists():
            clear_temp_state()
        comfort_off_action = "turned OFF (temp below desired and humidity below reset)"

    # --- Humidity-based Dry mode routine (runs second, skipped if the ------
    # --- temperature routine already took action this cycle) --------------
    saved = load_saved_state()
    print(f"Saved override state present: {saved is not None}")

    ac_in_dry = ac.state.mode == MIDEA_DRY_MODE and ac.state.running
    action_taken = "no action"

    if temp_action_taken != "no action":
        print(f"Temperature routine already took action this cycle "
              f"({temp_action_taken}) — skipping humidity routine.")
        action_taken = "skipped (temperature routine acted this cycle)"

    elif humidity > effective_trigger and saved is None:
        snapshot = current_snapshot(ac)
        save_state(snapshot)
        print(f"Humidity above {effective_trigger}% — saving state {snapshot} "
              f"and switching to dry mode.")
        ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
        action_taken = "turned ON (switched to dry mode)"

    elif humidity > effective_trigger and saved is not None and not ac_in_dry:
        print(f"Humidity still above {effective_trigger}% and an override is "
              f"recorded, but the AC isn't actually in dry mode (mode="
              f"{ac.state.mode} running={ac.state.running}) — something else "
              f"must have changed it. Re-asserting dry mode without touching "
              f"the saved original state.")
        ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
        action_taken = "turned ON (re-asserted dry mode)"

    elif humidity < effective_reset and saved is not None:
        print(f"Humidity below {effective_reset}% — restoring saved state {saved}.")
        if saved["mode"] == MIDEA_DRY_MODE:
            print("Saved state was itself Dry mode — turning off instead of "
                  "restoring Dry mode.")
            ac.set_state(running=False, cloud=cloud)
        else:
            ac.set_state(
                mode=saved["mode"],
                running=saved["running"],
                target_temperature=saved["target_temperature"],
                fan_speed=saved["fan_speed"],
                cloud=cloud,
            )
        clear_state()
        restored_mode_name = MIDEA_MODE_NAMES.get(saved["mode"], "Unknown")
        if saved["mode"] == MIDEA_DRY_MODE:
            action_taken = "turned OFF (saved state was Dry mode)"
        else:
            action_taken = (
                f"restored to ON ({restored_mode_name})" if saved["running"]
                else f"turned OFF (restored, was {restored_mode_name})"
            )

    else:
        print("No action needed this run.")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
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

        with open(github_output, "a") as f:
            f.write(f"humidity={humidity}\n")
            f.write(f"mode={ac.state.mode}\n")
            f.write(f"mode_name={mode_name}\n")
            f.write(f"running={ac.state.running}\n")
            f.write(f"target={ac.state.target_temperature}\n")
            f.write(f"target_f={target_f:.1f}\n")
            f.write(f"fan={ac.state.fan_speed}\n")
            f.write(f"govee_temp_f={govee_temp_f:.1f}\n")
            f.write(f"ac_indoor_f={ac_indoor_f:.1f}\n")
            f.write(f"trigger={effective_trigger}\n")
            f.write(f"reset={effective_reset}\n")
            f.write(f"action={action_taken}\n")
            f.write(f"action_occurred={'true' if action_taken != 'no action' else 'false'}\n")
            f.write(f"high_temp_alert={'true' if high_temp_alert else 'false'}\n")
            f.write(f"alert_message={alert_message}\n")
            f.write(f"temp_action={temp_action_taken}\n")
            f.write(f"temp_action_occurred={'true' if temp_action_taken != 'no action' else 'false'}\n")
            f.write(f"desired_temp_f={DESIRED_ROOM_TEMP_F}\n")
            f.write(f"temp_reset_f={temp_reset_f}\n")
            f.write(f"comfort_off_action={comfort_off_action}\n")
            f.write(f"comfort_off_occurred={'true' if comfort_off_action != 'no action' else 'false'}\n")


def main() -> int:
    govee_key = os.environ.get("GOVEE_API_KEY")
    midea_account = os.environ.get("MIDEA_ACCOUNT")
    midea_password = os.environ.get("MIDEA_PASSWORD")

    if not all([govee_key, midea_account, midea_password]):
        print("Missing one or more required env vars "
              "(GOVEE_API_KEY, MIDEA_ACCOUNT, MIDEA_PASSWORD).")
        return 1

    run_cycle(govee_key, midea_account, midea_password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
