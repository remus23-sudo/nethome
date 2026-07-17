"""
One-time diagnostic script for the Govee API.

Lists devices on the account (to find the exact sku/MAC for the H5103),
then requests its current state and prints the raw JSON response so we
can see the exact capability/instance names used for temperature and
humidity before wiring up the real automation.

Requires env var:
  GOVEE_API_KEY - Govee Developer API key
"""

import json
import os
import sys
import uuid

import requests

BASE_URL = "https://openapi.api.govee.com/router/api/v1"


def main() -> int:
    api_key = os.environ.get("GOVEE_API_KEY")
    if not api_key:
        print("GOVEE_API_KEY env var is not set.")
        return 1

    headers = {
        "Govee-API-Key": api_key,
        "Content-Type": "application/json",
    }

    print("Fetching device list...")
    resp = requests.get(f"{BASE_URL}/user/devices", headers=headers, timeout=15)
    print(f"GET /user/devices -> HTTP {resp.status_code}")
    try:
        devices_payload = resp.json()
    except ValueError:
        print("Response was not valid JSON:")
        print(resp.text)
        return 1

    print(json.dumps(devices_payload, indent=2))

    devices = devices_payload.get("data", [])
    if not devices:
        print("No devices returned on this account.")
        return 1

    print(f"\nFound {len(devices)} device(s). Requesting state for each...\n")

    for dev in devices:
        sku = dev.get("sku")
        device_id = dev.get("device")
        name = dev.get("deviceName")
        print("=" * 70)
        print(f"Device: {name}  sku={sku}  id={device_id}")

        body = {
            "requestId": str(uuid.uuid4()),
            "payload": {"sku": sku, "device": device_id},
        }
        state_resp = requests.post(
            f"{BASE_URL}/device/state", headers=headers, json=body, timeout=15
        )
        print(f"POST /device/state -> HTTP {state_resp.status_code}")
        try:
            state_payload = state_resp.json()
        except ValueError:
            print("Response was not valid JSON:")
            print(state_resp.text)
            continue

        print(json.dumps(state_payload, indent=2))

    print("\n" + "=" * 70)
    print("Done. Look through the state response(s) above for the")
    print("capability 'instance' names that carry temperature and")
    print("humidity values (e.g. under payload.capabilities[].instance")
    print("with a matching .state.value). Share what you see back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
