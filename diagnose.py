"""
One-time diagnostic script.

Connects to the Midea/NetHome Plus cloud using account credentials,
finds every appliance registered on the account, and prints every
attribute it can see. This is used to confirm the exact field name
for humidity (and to sanity-check mode/running/temperature fields)
before wiring up the real automation.

Run this once, share the printed output (redact the id/sn/mac if you
want), and the humidity-based automation script will be written using
the confirmed field names instead of guesses.

Requires env vars:
  MIDEA_ACCOUNT   - NetHome Plus account email
  MIDEA_PASSWORD  - NetHome Plus account password
"""

import os
import sys

from midea_beautiful import appliance_state, connect_to_cloud, find_appliances


def dump(label: str, obj) -> None:
    print(f"-- {label} --")
    try:
        items = vars(obj).items()
    except TypeError:
        print(f"  repr: {obj!r}")
        return
    for key, value in sorted(items):
        print(f"  {key!r}: {value!r}")


def main() -> int:
    account = os.environ.get("MIDEA_ACCOUNT")
    password = os.environ.get("MIDEA_PASSWORD")

    if not account or not password:
        print("MIDEA_ACCOUNT and/or MIDEA_PASSWORD env vars are not set.")
        return 1

    print("Connecting to the Midea/NetHome Plus cloud...")
    try:
        cloud = connect_to_cloud(account=account, password=password)
    except Exception as e:
        print(f"Failed to connect to cloud: {e!r}")
        return 1
    print("Connected.")

    print("\nFinding appliances registered on this account...")
    try:
        appliances = find_appliances(account=account, password=password)
    except Exception as e:
        print(f"find_appliances() failed: {e!r}")
        return 1

    if not appliances:
        print("No appliances found on this account.")
        return 1

    print(f"Found {len(appliances)} appliance(s).\n")

    for appliance in appliances:
        print("=" * 70)
        print(f"repr(): {appliance!r}")
        dump("attributes from find_appliances()", appliance)

        appliance_id = getattr(appliance, "id", None)
        if appliance_id is None:
            print("  (no id attribute found, cannot refresh via cloud)")
            continue

        print()
        try:
            refreshed = appliance_state(cloud=cloud, id=appliance_id)
        except Exception as e:
            print(f"  appliance_state(cloud=..., id={appliance_id}) failed: {e!r}")
            continue

        print(f"repr() after cloud refresh: {refreshed!r}")
        dump("attributes after cloud refresh", refreshed)
        print()

    print("=" * 70)
    print("Done. Look through the attribute dumps above for anything")
    print("humidity-related (e.g. 'humidity', 'indoor_humidity', 'rh').")
    print("Share the field name(s) you see so the real automation can")
    print("be written against them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
