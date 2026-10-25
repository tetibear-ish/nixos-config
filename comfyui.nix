{ lib, pkgs, ... }:
{
  services.comfyui = {
    enable       = true;
    acceleration = lib.mkDefault false;  # CPU unless the host opts in (nixos uses ROCm)
    host         = "0.0.0.0";
    port         = 8188;
    home         = "/var/lib/comfyui";
    openFirewall = false;    # Tailscale trusted interface covers this
    # Without this, nixified-ai's wrapper reads models/, custom_nodes/, input/
    # and output/ from $HOME/.local/share/comfyui instead of the dirs below.
    extraFlags   = [ "--base-directory" "/var/lib/comfyui" ];
  };

  # Run as tetibear so models can be dropped in directly
  systemd.services.comfyui.serviceConfig = {
    User        = lib.mkForce "tetibear";
    Group       = lib.mkForce "users";
    DynamicUser = lib.mkForce false;
  };

  # Start/stop from Emacs (`SPC s s`) or `systemctl` without a password.
  managedServices = [ "comfyui" ];

  # Kept for ~/.local/bin/comfyui-launch.sh, which uses sudo.
  security.sudo.extraRules = [{
    users = [ "tetibear" ];
    commands = [
      { command = "/run/current-system/sw/bin/systemctl start comfyui";   options = [ "NOPASSWD" ]; }
      { command = "/run/current-system/sw/bin/systemctl stop comfyui";    options = [ "NOPASSWD" ]; }
      { command = "/run/current-system/sw/bin/systemctl restart comfyui"; options = [ "NOPASSWD" ]; }
    ];
  }];

  # Pre-create model subdirectories owned by tetibear
  system.activationScripts.comfyui-models.text = ''
    for dir in checkpoints loras vae controlnet clip unet upscale_models \
               diffusion_models text_encoders clip_vision; do
      mkdir -p /var/lib/comfyui/models/$dir
    done
    mkdir -p /var/lib/comfyui/output /var/lib/comfyui/input /var/lib/comfyui/custom_nodes \
             /var/lib/comfyui/user/default/workflows
    chown -R tetibear:users /var/lib/comfyui
  '';
}
