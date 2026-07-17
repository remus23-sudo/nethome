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

GOVEE_SKU = "H5103"
GOVEE_DEVICE = "A0:38:E6:E9:C0:46:12:59"
GOVEE_BASE_URL = "https://openapi.api.govee.com/router/api/v1"

MIDEA_DRY_MODE = 3

STATE_FILE = Path("state.json")


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


# --- Main -----------------------------------------------------------------

def main() -> int:
    govee_key = os.environ.get("GOVEE_API_KEY")
    midea_account = os.environ.get("MIDEA_ACCOUNT")
    midea_password = os.environ.get("MIDEA_PASSWORD")

    if not all([govee_key, midea_account, midea_password]):
        print("Missing one or more required env vars "
              "(GOVEE_API_KEY, MIDEA_ACCOUNT, MIDEA_PASSWORD).")
        return 1

    humidity, govee_temp_f = get_govee_reading(govee_key)
    cloud, ac = get_ac(midea_account, midea_password)
    saved = load_saved_state()

    print(f"Humidity: {humidity}%")
    print(f"AC state: mode={ac.state.mode} running={ac.state.running} "
          f"target={ac.state.target_temperature} fan={ac.state.fan_speed}")
    print(f"Saved override state present: {saved is not None}")

    ac_in_dry = ac.state.mode == MIDEA_DRY_MODE and ac.state.running
    action_taken = "no action"

    if humidity > HUMIDITY_TRIGGER and saved is None:
        snapshot = current_snapshot(ac)
        save_state(snapshot)
        print(f"Humidity above {HUMIDITY_TRIGGER}% — saving state {snapshot} "
              f"and switching to dry mode.")
        ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
        action_taken = "turned ON (switched to dry mode)"

    elif humidity > HUMIDITY_TRIGGER and saved is not None and not ac_in_dry:
        print(f"Humidity still above {HUMIDITY_TRIGGER}% and an override is "
              f"recorded, but the AC isn't actually in dry mode (mode="
              f"{ac.state.mode} running={ac.state.running}) — something else "
              f"must have changed it. Re-asserting dry mode without touching "
              f"the saved original state.")
        ac.set_state(mode=MIDEA_DRY_MODE, running=True, cloud=cloud)
        action_taken = "turned ON (re-asserted dry mode)"

    elif humidity < HUMIDITY_RESET and saved is not None:
        print(f"Humidity below {HUMIDITY_RESET}% — restoring saved state {saved}.")
        ac.set_state(
            mode=saved["mode"],
            running=saved["running"],
            target_temperature=saved["target_temperature"],
            fan_speed=saved["fan_speed"],
            cloud=cloud,
        )
        clear_state()
        action_taken = (
            "restored to ON" if saved["running"] else "turned OFF (restored)"
        )

    else:
        print("No action needed this run.")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"humidity={humidity}\n")
            f.write(f"mode={ac.state.mode}\n")
            f.write(f"running={ac.state.running}\n")
            f.write(f"target={ac.state.target_temperature}\n")
            f.write(f"fan={ac.state.fan_speed}\n")
            f.write(f"govee_temp_f={govee_temp_f:.1f}\n")
            f.write(f"action={action_taken}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
