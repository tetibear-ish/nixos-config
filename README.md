# nixos-config

NixOS configuration for a machine that isn't installed yet — currently
running Bazzite, planning to distro-hop to NixOS to experiment with
GPU passthrough (VFIO).

## Files

- `vfio.nix` — draft VFIO/GPU-passthrough section (IOMMU, vfio-pci binding,
  libvirtd/QEMU/OVMF) to merge into the real `configuration.nix` once
  NixOS is actually installed. See the comments in the file for hardware
  details, verification steps, and follow-up tuning (IOMMU group check,
  CPU pinning for the 7950X3D's asymmetric CCDs, hugepages).

## Hardware target

- CPU: AMD Ryzen 9 7950X3D
- Host GPU: AMD RX 6800 XT (or the Raphael iGPU)
- Passthrough GPU: Nvidia GTX 1050 Ti (`10de:1c82` / `10de:0fb9`)

## Status

Pre-install planning. Once NixOS is actually installed, this repo should
hold the real `configuration.nix` (and `hardware-configuration.nix`,
kept out of git if it's machine-specific enough to not matter, or kept
in if you want a record of it).
