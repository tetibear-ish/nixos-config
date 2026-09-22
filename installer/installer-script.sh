#!/usr/bin/env bash
# kawaii-installer: guided Desktop/Server NixOS install onto this machine.
# Launched automatically on tty1 login (see iso-configuration.nix).
set -euo pipefail

REPO_URL="https://github.com/tetibear-ish/nixos-config.git"
WORK="/root/nixos-config-work"

# Palette + kaomoji, ported from kawaii.zsh-theme so the installer stays on-brand.
C_BLUE=4; C_CYAN=6; C_MAGENTA=5; C_YELLOW=3; C_GREEN=2; C_RED=1

HAPPY_FACES=(
  "(˶ᵔ ᵕ ᵔ˶)"
  "(｡•̀ᴗ-)✧"
  "(≧▽≦)"
  "(´꒳\`)"
  "(๑˃ᴗ˂)ﻭ"
)
ANGRY_FACES=(
  "(╬ Ò﹏Ó)"
  "(＃\`Д\`)"
  "( \` ω ´ )"
  "٩(ఠ益ఠ)۶"
  "(¬_¬)"
)

random_face() {
  local -n arr="$1"
  echo "${arr[$((RANDOM % ${#arr[@]}))]}"
}

sky_emoji() {
  local hour
  hour=$((10#$(date +%H)))
  if (( hour >= 6 && hour < 11 )); then echo "🌅"
  elif (( hour >= 11 && hour < 17 )); then echo "☀️"
  elif (( hour >= 17 && hour < 20 )); then echo "🌇"
  else echo "🌙"
  fi
}

die() {
  # Writes straight to the controlling terminal (not stdout) and exits the
  # *whole* script, not just a subshell — safe to call from anywhere,
  # including from inside a function whose stdout is being captured via $().
  gum style --foreground "$C_RED" --border rounded --padding "1 2" \
    "$(random_face ANGRY_FACES) $1" > /dev/tty
  kill -TERM "$SCRIPT_PID"
  exit 1
}
SCRIPT_PID=$$
trap 'die "Something went wrong. Aborting before going any further."' ERR

status() {
  # A plain status line, not gum spin — spin pipes the wrapped command's
  # stdout/stderr to render its animation, and real tools (git, parted)
  # sometimes fail outright when they detect a non-terminal fd that way.
  gum style --foreground "$C_CYAN" "→ $1"
}

welcome() {
  gum style --foreground "$C_BLUE" --border rounded --padding "1 2" --align center \
    "$(sky_emoji) $(random_face HAPPY_FACES)" \
    "" \
    "kawaii-installer" \
    "let's get this machine onto the network~"
}

# --- steps ---------------------------------------------------------------

pick_profile() {
  gum choose --header "Desktop or Server install?" "Desktop" "Server"
}

pick_hostname() {
  local name
  while true; do
    name=$(gum input --placeholder "hostname (lowercase letters/digits/hyphens)")
    if [[ ! "$name" =~ ^[a-z][a-z0-9-]*$ ]]; then
      gum style --foreground "$C_RED" "Invalid hostname. Try again." > /dev/tty
      continue
    fi
    if grep -qE "^\s*${name}\s*=" "$WORK/flake.nix"; then
      gum style --foreground "$C_RED" "A host named '$name' already exists in flake.nix. Pick another." > /dev/tty
      continue
    fi
    echo "$name"
    return
  done
}

pick_disk() {
  local choice disk
  # -e2,7,11 excludes floppy, loop, and CD-ROM devices from the list.
  choice=$(lsblk -dn -o NAME,SIZE,MODEL -e2,7,11 | sed 's/^/\/dev\//' | \
    gum choose --header "Select the install disk — ALL DATA ON IT WILL BE ERASED")
  disk=$(awk '{print $1}' <<< "$choice")

  if lsblk -no MOUNTPOINT "$disk" | grep -q .; then
    die "$disk (or a partition on it) is currently mounted. Unmount it first."
  fi

  lsblk "$disk" > /dev/tty
  local confirm
  confirm=$(gum input --placeholder "Type $disk to confirm ERASING it")
  [[ "$confirm" == "$disk" ]] || die "Confirmation didn't match. Aborting, nothing was touched."

  echo "$disk"
}

partition_and_mount() {
  local disk="$1" suffix esp root
  if [[ "$disk" =~ [0-9]$ ]]; then suffix="p"; else suffix=""; fi
  esp="${disk}${suffix}1"
  root="${disk}${suffix}2"

  status "Partitioning $disk..."
  parted --script "$disk" -- mklabel gpt mkpart ESP fat32 1MiB 513MiB set 1 esp on mkpart root ext4 513MiB 100%
  partprobe "$disk" || true
  udevadm settle

  status "Formatting..."
  mkfs.fat -F32 -n BOOT "$esp"
  mkfs.ext4 -L nixos -F "$root"
  sync
  udevadm settle

  mount "$root" /mnt
  mkdir -p /mnt/boot
  mount "$esp" /mnt/boot
}

# --- main ------------------------------------------------------------------

welcome

profile=$(pick_profile)
case "$profile" in
  Desktop) profile_file="./desktop.nix" ;;
  Server)  profile_file="./server.nix" ;;
esac

status "Cloning nixos-config..."
git clone --depth 1 -q "$REPO_URL" "$WORK"

hostname=$(pick_hostname)
disk=$(pick_disk)

partition_and_mount "$disk"

status "Scanning hardware..."
nixos-generate-config --root /mnt
cp /mnt/etc/nixos/hardware-configuration.nix "$WORK/hardware-${hostname}.nix"

entry="    ${hostname} = { profile = ${profile_file}; extraModules = [ ]; };"
sed -i "/# INSTALLER:/a\\${entry}" "$WORK/flake.nix"

git -C "$WORK" add -A
if ! nix --extra-experimental-features "nix-command flakes" flake check "$WORK" --no-build; then
  die "The generated flake config didn't evaluate. The disk was partitioned, but nothing was installed. Fix $WORK/flake.nix (or hardware-${hostname}.nix) and re-run 'nixos-install --root /mnt --flake $WORK#${hostname}' by hand."
fi
git -C "$WORK" -c user.name="installer-bot" -c user.email="installer@localhost" \
  commit -m "add host ${hostname} (${profile})" --quiet

status "Installing NixOS ($profile)... this takes a while"
nixos-install --root /mnt --flake "$WORK#${hostname}"

mkdir -p /mnt/root
cp -a "$WORK" "/mnt/root/nixos-config-new"

gum style --foreground "$C_GREEN" --border rounded --padding "1 2" \
  "$(random_face HAPPY_FACES) All done!" \
  "" \
  "Reboot into '${hostname}' whenever you're ready." \
  "" \
  "This install used free software only (Steam, Discord, Chrome, etc." \
  "were skipped). Add './unfree.nix' to ${hostname}'s extraModules in" \
  "flake.nix and rebuild whenever you want those." \
  "" \
  "Your new flake entry was committed locally but NOT pushed" \
  "(this installer has no git credentials). After first boot:" \
  "  cd /root/nixos-config-new" \
  "  git push   # using your own GitHub credentials/SSH key"
