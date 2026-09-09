# VFIO / GPU passthrough section for configuration.nix
#
# Target hardware (see conversation for full lspci -nn output):
#   Host CPU:     AMD Ryzen 9 7950X3D (amd_iommu, not intel_iommu)
#   Host GPU:     AMD RX 6800 XT (03:00.0 / 03:00.1) -- OR the Raphael iGPU
#                 (19:00.0) if you'd rather keep the 6800 XT fully on the host
#   Passthrough:  Nvidia GTX 1050 Ti -- 05:00.0 (10de:1c82)
#                 + its HD Audio function -- 05:00.1 (10de:0fb9)
#
# This is a section to merge into your real configuration.nix, not a
# standalone file -- drop these attributes into your existing config.
# Replace `zz` below with your actual NixOS username if different.

{ config, pkgs, ... }:

{
  # --- IOMMU + early vfio-pci binding -----------------------------------
  boot.kernelParams = [
    "amd_iommu=on"
    "iommu=pt"          # passthrough mode: host devices you AREN'T passing
                         # through keep near-native IOMMU performance
  ];

  boot.kernelModules = [ "vfio_pci" "vfio" "vfio_iommu_type1" "vfio_virqfd" ];

  # Grab the 1050 Ti (both the GPU and its audio function) for vfio-pci
  # before any other driver gets a chance to bind to it.
  boot.extraModprobeConfig = ''
    options vfio-pci ids=10de:1c82,10de:0fb9
  '';

  # Belt-and-suspenders: stop the host from ever trying to load nouveau
  # for this card at all. Safe here since there's only one Nvidia GPU
  # in the system -- if you ever add a second Nvidia card you don't want
  # passed through, remove this and rely on vfio-pci.ids binding order
  # instead.
  boot.blacklistedKernelModules = [ "nouveau" ];

  # --- libvirt / QEMU / OVMF (UEFI) --------------------------------------
  virtualisation.libvirtd = {
    enable = true;
    qemu = {
      package = pkgs.qemu_kvm;
      runAsRoot = true;
      swtpm.enable = true;       # virtual TPM -- needed for Windows 11 guests
      ovmf = {
        enable = true;
        packages = [ pkgs.OVMFFull.fd ];
      };
    };
  };

  programs.virt-manager.enable = true;
  programs.dconf.enable = true;  # virt-manager needs this for its settings

  users.users.zz.extraGroups = [ "libvirtd" "kvm" ];

  # --- Notes / next steps -------------------------------------------------
  # 1. After the first boot with this applied, confirm the 1050 Ti actually
  #    landed on vfio-pci and not nouveau/nvidia:
  #      lspci -nnk -d 10de:1c82
  #    -> "Kernel driver in use: vfio-pci"
  #
  # 2. Check its IOMMU group is clean (ideally just the GPU + its audio
  #    function, nothing else):
  #      for d in /sys/kernel/iommu_groups/*/devices/*; do
  #        n=${d#*/iommu_groups/}; n=${n%%/*}
  #        echo "Group $n: $(basename $d) $(lspci -nns ${d##*/})"
  #      done | sort -t' ' -k2 -n
  #    If other, unrelated devices share the 1050 Ti's group, you're into
  #    ACS-override-patch territory (controversial, has real security/
  #    stability tradeoffs -- worth a separate conversation before going
  #    there).
  #
  # 3. Your 7950X3D has two CCDs with asymmetric cache (one V-cache, one
  #    higher-clocked). For a gaming VM you'll likely want to pin vCPUs to
  #    one CCD's cores specifically, the same way Windows' own scheduler
  #    prefers the V-cache CCD for games. This needs `lscpu -e` on the
  #    actual installed system to map core/thread IDs to CCD, and belongs
  #    in the VM's libvirt XML (<cputune> pinning), not here -- happy to
  #    do that once you're installed and can share `lscpu -e` output.
  #
  # 4. This config doesn't set up hugepages or isolcpus/nohz_full for the
  #    VM -- both are worthwhile once you have things working end to end,
  #    but add complexity (and a real risk of a host that won't boot if
  #    isolcpus is misconfigured) that's better tackled as a second pass.
}
