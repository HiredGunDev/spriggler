#!/usr/bin/env python3
"""Exercise a VeSync Dual 200S humidifier.

Finds the humidifier by name, cycles through high/low/off with
30-second holds, printing status at each step.

The Dual 200S has exactly 2 mist levels: 1 (low) and 2 (high).

Usage:
    python vesync_exercise.py --email YOU@EMAIL --password SECRET
    python vesync_exercise.py --email YOU@EMAIL --password SECRET --name "Dual 200S"

Or set VESYNC_EMAIL and VESYNC_PASSWORD env vars.
"""

import argparse
import asyncio
import os
import sys


async def main():
    parser = argparse.ArgumentParser(description='Exercise VeSync humidifier')
    parser.add_argument('--email', default=os.environ.get('VESYNC_EMAIL'))
    parser.add_argument('--password', default=os.environ.get('VESYNC_PASSWORD'))
    parser.add_argument('--name', default='Dual 200S',
                        help='Device name in VeSync app')
    args = parser.parse_args()

    if not args.email or not args.password:
        print("Error: --email and --password required "
              "(or set VESYNC_EMAIL/VESYNC_PASSWORD)")
        sys.exit(1)

    from pyvesync import VeSync

    print(f"Connecting to VeSync as {args.email}...")
    async with VeSync(args.email, args.password) as mgr:
        await mgr.login()
        if not mgr.enabled:
            print("Login failed.")
            sys.exit(1)
        print("Login OK.")

        await mgr.update()
        humidifiers = mgr.devices.humidifiers
        print(f"Found {len(humidifiers)} humidifier(s):")
        for h in humidifiers:
            print(f"  {h.device_name} ({h.device_type}) "
                  f"on={h.is_on} levels={h.mist_levels}")

        # Find target
        device = None
        for h in humidifiers:
            if h.device_name == args.name:
                device = h
                break

        if device is None:
            names = [h.device_name for h in humidifiers]
            print(f"\nHumidifier '{args.name}' not found. "
                  f"Available: {names}")
            sys.exit(1)

        levels = device.mist_levels
        low = levels[0]
        high = levels[-1]
        print(f"\nUsing: {device.device_name}")
        print(f"Mist levels: {levels} (low={low}, high={high})")

        async def show_state(label):
            await asyncio.sleep(2)  # let cloud state settle
            await device.update()
            print(f"  [{label}] on={device.is_on} "
                  f"mist={device.state.mist_virtual_level}")

        # ── High ─────────────────────────────────────────────
        print(f"\n── Setting HIGH (level {high}) ──")
        await device.turn_on()
        await device.set_mode('manual')
        ok = await device.set_mist_level(high)
        print(f"  set_mist_level({high}) returned {ok}")
        await show_state("after high")
        print("  Holding 30s...")
        await asyncio.sleep(30)

        # ── Low ──────────────────────────────────────────────
        print(f"\n── Setting LOW (level {low}) ──")
        ok = await device.set_mist_level(low)
        print(f"  set_mist_level({low}) returned {ok}")
        await show_state("after low")
        print("  Holding 30s...")
        await asyncio.sleep(30)

        # ── Off ──────────────────────────────────────────────
        print("\n── Turning OFF ──")
        ok = await device.turn_off()
        print(f"  turn_off() returned {ok}")
        await show_state("after off")
        print("  Holding 30s...")
        await asyncio.sleep(30)

        # ── Final status ─────────────────────────────────────
        await device.update()
        print(f"\n── Final state ──")
        print(f"  on={device.is_on}")
        print(f"  mist_level={device.state.mist_virtual_level}")
        print("\nDone.")


if __name__ == '__main__':
    asyncio.run(main())