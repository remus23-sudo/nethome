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

import inspect
import os
import sys

from midea_beautiful import appliance_state, connect_to_cloud, find_appliances

print("Installed appliance_state signature:")
print(f"  {inspect.signature(appliance_state)}")
print("Installed connect_to_cloud signature:")
print(f"  {inspect.signature(connect_to_cloud)}")
print("Installed find_appliances signature:")
print(f"  {inspect.signature(find_appliances)}")
print()


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

        state_obj = getattr(appliance, "state", None)
        if state_obj is not None:
            dump("attributes on appliance.state", state_obj)

        appliance_id = (
            getattr(appliance, "id", None)
            or getattr(state_obj, "id", None)
            or getattr(state_obj, "_id", None)
        )
        if appliance_id is None:
            print("  (no id found on appliance or appliance.state, cannot refresh)")
            continue

        print()
        print("  Calling appliance_state(cloud=..., use_cloud=True, "
              "appliance_id=..., appliance_type='0xAC')")
        try:
            refreshed = appliance_state(
                cloud=cloud,
                use_cloud=True,
                appliance_id=appliance_id,
                appliance_type="0xAC",
            )
        except Exception as e:
            print(f"  appliance_state(...) failed: {e!r}")
            continue

        print(f"repr() after cloud refresh: {refreshed!r}")
        dump("attributes after cloud refresh", refreshed)
        refreshed_state = getattr(refreshed, "state", None)
        if refreshed_state is not None:
            dump("attributes on refreshed.state", refreshed_state)

        print("-- methods available on refreshed (LanDevice) --")
        for m in sorted(dir(refreshed)):
            if not m.startswith("_"):
                print(f"  {m}")
        if refreshed_state is not None:
            print("-- methods available on refreshed.state --")
            for m in sorted(dir(refreshed_state)):
                if not m.startswith("_"):
                    print(f"  {m}")
        print()

    # Hunt for any raw/unparsed cloud response that might still carry a
    # humidity field the library's Appliance classes don't expose.
    print("=" * 70)
    print("Looking for raw cloud response data cached on the cloud object...")
    for key, value in vars(cloud).items():
        text = repr(value)
        if len(text) > 2000:
            text = text[:2000] + "... [truncated]"
        print(f"  cloud.{key!r}: {text}")

    print("=" * 70)
    print("Done. Look through the attribute dumps above for anything")
    print("humidity-related (e.g. 'humidity', 'indoor_humidity', 'rh').")
    print("Share the field name(s) you see so the real automation can")
    print("be written against them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
