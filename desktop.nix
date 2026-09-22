# Desktop profile: KDE Plasma6 / Hyprland, free GUI apps, audio.
# Unfree apps (Steam, Discord, Chrome, etc.) live in unfree.nix instead.
{ config, pkgs, ... }:

{
  imports = [ ./common.nix ];

  services.displayManager.sddm = {
    enable = true;
    wayland.enable = true;
  };
  services.desktopManager.plasma6.enable = true;
  programs.hyprland.enable = true;

  fonts.packages = with pkgs; [
    nerd-fonts.hack
    powerline-fonts
    powerline
  ];

  # Enable the X11 windowing system.
  services.xserver.enable = true;

  # Enable the Budgie Desktop environment.
  #services.xserver.displayManager.lightdm.enable = true;
  #services.desktopManager.budgie.enable = true;

  # Configure keymap in X11
  services.xserver.xkb = {
    layout = "us";
    variant = "";
    options = "ctrl:nocaps";
  };

  # Enable CUPS to print documents.
  services.printing.enable = true;

  # Enable sound with pipewire.
  services.pulseaudio.enable = false;
  security.rtkit.enable = true;
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    alsa.support32Bit = true;
    pulse.enable = true;

    # RNNoise filter chain: creates a virtual "Noise-Cancelled Mic" source
    # Select it as your microphone in Discord (or any app) via Settings → Voice
    extraConfig.pipewire."99-rnnoise" = {
      "context.modules" = [
        {
          name = "libpipewire-module-filter-chain";
          args = {
            "node.description" = "Noise-Cancelled Mic";
            "media.name" = "Noise-Cancelled Mic";
            "filter.graph" = {
              nodes = [
                {
                  type = "ladspa";
                  name = "rnnoise";
                  plugin = "${pkgs.rnnoise-plugin}/lib/ladspa/librnnoise_ladspa.so";
                  label = "noise_suppressor_mono";
                  control = { "VAD Threshold (%)" = 50; };
                }
              ];
            };
            "capture.props" = {
              "node.name" = "capture.rnnoise_source";
              "node.passive" = true;
              "audio.rate" = 48000;
            };
            "playback.props" = {
              "node.name" = "rnnoise_source";
              "media.class" = "Audio/Source";
              "audio.rate" = 48000;
            };
          };
        }
      ];
    };
  };

  # Enable touchpad support (enabled default in most desktopManager).
  # services.xserver.libinput.enable = true;

  # Install firefox.
  programs.firefox.enable = true;

  environment.systemPackages = with pkgs; [
    sweet
    wl-clipboard
    blender
    prismlauncher
    jre8
    (python3.withPackages (ps: with ps; [ pip ]))
    codex
    kitty
    waybar
    wofi
    hyprpaper
    hyprlock
  ];

  # OBS via Flatpak gets the proper com.obsproject.Studio app ID
  # which is required for xdg-desktop-portal ScreenCast to work on Wayland.
  # After rebuild: flatpak install flathub com.obsproject.Studio
  services.flatpak.enable = true;

  xdg.portal = {
    enable = true;
    extraPortals = [ pkgs.kdePackages.xdg-desktop-portal-kde ];
    config.common.default = "kde";
  };

  hardware.graphics = {
    enable = true;
    enable32Bit = true;
  };
}
