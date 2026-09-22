#!/usr/bin/env bash
# test-iso: boot the kawaii-installer ISO (or a previously-installed test
# disk) in a throwaway QEMU VM with UEFI firmware. Never touches real disks.
set -euo pipefail

usage() {
  echo "usage: test-iso [iso-file] [disk-file]" >&2
  echo "       test-iso --boot <disk-file>   # boot an already-installed test disk, no ISO" >&2
  echo "" >&2
  echo "iso-file defaults to the single ./result/iso/*.iso in the current directory." >&2
  echo "disk-file, if given, persists after the VM exits (so you can re-run against" >&2
  echo "the same disk to test a post-install boot); if omitted, a throwaway disk is" >&2
  echo "created and deleted automatically." >&2
  exit 1
}

boot_only=false
if [[ "${1:-}" == "--boot" ]]; then
  boot_only=true
  shift
fi

ovmf="$(nix eval --raw "nixpkgs#OVMF.fd.outPath")/FV/OVMF.fd"

cleanup_disk=false
disk=""
iso=""

if $boot_only; then
  disk="${1:-}"
  [[ -n "$disk" && -f "$disk" ]] || { echo "error: --boot needs an existing disk-file" >&2; usage; }
else
  iso="${1:-}"
  if [[ -z "$iso" ]]; then
    mapfile -t isos < <(find result/iso -maxdepth 1 -name '*.iso' 2>/dev/null)
    if [[ ${#isos[@]} -eq 1 ]]; then
      iso="${isos[0]}"
    else
      echo "error: no ISO given and ./result/iso/*.iso didn't resolve to exactly one file" >&2
      usage
    fi
  fi
  [[ -f "$iso" ]] || { echo "error: '$iso' is not a file" >&2; exit 1; }

  disk="${2:-}"
  if [[ -z "$disk" ]]; then
    disk="$(mktemp --suffix=.qcow2)"
    cleanup_disk=true
  fi
  # -s (non-empty), not -f: mktemp already creates an empty file, and a
  # user-given path might also point at an empty/nonexistent file.
  if [[ ! -s "$disk" ]]; then
    qemu-img create -f qcow2 "$disk" 20G
  fi
fi

cleanup() {
  if $cleanup_disk; then
    rm -f "$disk"
  fi
}
trap cleanup EXIT

# shellcheck disable=SC2054  # commas here are inside quoted qemu arg values, not element separators
qemu_args=(
  -enable-kvm -m 4096 -smp 2
  -drive "file=$disk,format=qcow2,if=virtio"
  -bios "$ovmf"
  -netdev user,id=net0 -device virtio-net,netdev=net0
)

if $boot_only; then
  echo "Booting $disk directly (no installer ISO)..."
else
  qemu_args+=(-cdrom "$iso" -boot d)
  echo "Booting $iso against disk $disk..."
fi

qemu-system-x86_64 "${qemu_args[@]}"
