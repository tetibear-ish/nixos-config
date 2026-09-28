{ lib, pkgs, ... }:
{
  services.comfyui = {
    enable       = true;
    acceleration = false;    # CPU only — no GPU on hoshimi
    host         = "0.0.0.0";
    port         = 8188;
    home         = "/var/lib/comfyui";
    openFirewall = false;    # Tailscale trusted interface covers this
  };

  # Run as tetibear so models can be dropped in directly
  systemd.services.comfyui.serviceConfig = {
    User        = lib.mkForce "tetibear";
    Group       = lib.mkForce "users";
    DynamicUser = lib.mkForce false;
  };

  # Pre-create model subdirectories owned by tetibear
  system.activationScripts.comfyui-models.text = ''
    for dir in checkpoints loras vae controlnet clip unet upscale_models; do
      mkdir -p /var/lib/comfyui/models/$dir
    done
    mkdir -p /var/lib/comfyui/output /var/lib/comfyui/input /var/lib/comfyui/custom_nodes
    chown -R tetibear:users /var/lib/comfyui
  '';
}
