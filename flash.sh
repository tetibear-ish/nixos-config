#!/usr/bin/env bash
# flash <iso-file> <device>: write an ISO to a disk with the same safety
# guards as the kawaii-installer (mounted check, root-disk check, typed
# confirmation) since dd to the wrong device is unrecoverable.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: flash <iso-file> <device>" >&2
  echo "example: flash result/iso/nixos.iso /dev/sda" >&2
  exit 1
fi

iso="$1"
device="$2"

if [[ ! -f "$iso" ]]; then
  echo "error: '$iso' is not a file" >&2
  exit 1
fi

if [[ ! -b "$device" ]]; then
  echo "error: '$device' is not a block device" >&2
  exit 1
fi

if lsblk -no MOUNTPOINT "$device" | grep -q .; then
  echo "error: $device (or a partition on it) is currently mounted. Unmount it first." >&2
  exit 1
fi

root_pkname="$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null || true)"
if [[ -n "$root_pkname" && "$device" == "/dev/$root_pkname" ]]; then
  echo "error: $device looks like your system disk. Refusing." >&2
  exit 1
fi

echo "About to overwrite: $device"
lsblk "$device"
echo
read -r -p "Type '$device' to confirm ERASING it: " confirm
if [[ "$confirm" != "$device" ]]; then
  echo "Confirmation didn't match. Aborting, nothing was touched." >&2
  exit 1
fi

dd if="$iso" of="$device" bs=4M status=progress conv=fsync
sync
echo "Done. Wrote $iso to $device."
