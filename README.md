# nixos-config

NixOS flake configuration for two machines: **hoshimi** and **nixos**.

Once set up, each machine automatically pulls the latest config from GitHub and runs `nixos-rebuild switch` on every boot. See **First-time setup** below to onboard a new machine.

## Machines

| Host | Hardware |
|------|----------|
| `hoshimi` | AMD Ryzen 9 7950X3D, RX 6800 XT |
| `nixos` | AMD (see `hardware-nixos.nix`) |

## Files

- `flake.nix` — defines both machine configurations
- `configuration.nix` — shared config for all machines
- `hardware-hoshimi.nix` — hoshimi hardware (tracked, not secret)
- `hardware-nixos.nix` — nixos hardware (tracked, not secret)
- `vfio.nix` — draft VFIO/GPU-passthrough config (commented out in imports)
- `kawaii.zsh-theme` — oh-my-zsh theme

## Workflow

**Making a change:**
1. Edit `configuration.nix` (or any other file) on either machine
2. `git commit` and `git push`
3. The change will apply automatically on next boot of all machines

**Applying immediately without rebooting:**
```bash
sudo nixos-rebuild switch --flake ~/nixos-config#hoshimi   # on hoshimi
sudo nixos-rebuild switch --flake ~/nixos-config#nixos     # on nixos
```

**Updating nixpkgs (to get package updates):**
```bash
nix flake update ~/nixos-config
git add flake.lock && git commit -m "update nixpkgs" && git push
```
Then rebuild or reboot to apply.

**First-time setup on a new machine:**
1. Add its hardware config to the repo: `nixos-generate-config --show-hardware-config > hardware-<hostname>.nix`
2. Add a `nixosConfigurations.<hostname>` entry in `flake.nix`
3. Commit and push
4. On the machine: `sudo nixos-rebuild switch --flake github:tetibear-ish/nixos-config#<hostname> --no-write-lock-file`
