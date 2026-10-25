# nixos-only services.
{ lib, ... }:

{
  # ComfyUI on the RX 6800 XT (gfx1030) via ROCm.
  services.comfyui = {
    acceleration = "rocm";
    # ROCm also enumerates the 7950X3D's iGPU (gfx1036), which nixpkgs' torch
    # has no kernels for: touching it aborts with "LLVM ERROR: CachedFileStream
    # was not committed". Pin the dGPU by UUID (from `rocminfo`) so a changed
    # enumeration order can't pick the iGPU.
    environmentVariables.ROCR_VISIBLE_DEVICES = "GPU-031dae45da0f9e9c";
  };

  # Desktop GPU is shared with games: don't start at boot, start on demand
  # (Emacs `SPC s s`, or `systemctl start comfyui`).
  systemd.services.comfyui.wantedBy = lib.mkForce [ ];
}
