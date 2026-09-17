# Edit this configuration file to define what should be installed on
# your system.  Help is available in the configuration.nix(5) man page
# and in the NixOS manual (accessible by running ‘nixos-help’).

{ config, pkgs, ... }:

{
  imports =
    [ # ./vfio.nix
    ];
  services.displayManager.sddm = {
    enable = true;
    wayland.enable = true;
  };
  programs.hyprland.enable = true;
  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # networking.wireless.enable = true;  # Enables wireless support via wpa_supplicant.

  # Configure network proxy if necessary
  # networking.proxy.default = "http://user:password@proxy:port/";
  # networking.proxy.noProxy = "127.0.0.1,localhost,internal.domain";

  # Enable networking
  networking.networkmanager.enable = true;

  fonts.packages = with pkgs; [
    nerd-fonts.hack
    powerline-fonts
    powerline
  ];

  # Set your time zone.
  time.timeZone = "America/New_York";

  # Select internationalisation properties.
  i18n.defaultLocale = "en_US.UTF-8";

  i18n.extraLocaleSettings = {
    LC_ADDRESS = "en_US.UTF-8";
    LC_IDENTIFICATION = "en_US.UTF-8";
    LC_MEASUREMENT = "en_US.UTF-8";
    LC_MONETARY = "en_US.UTF-8";
    LC_NAME = "en_US.UTF-8";
    LC_NUMERIC = "en_US.UTF-8";
    LC_PAPER = "en_US.UTF-8";
    LC_TELEPHONE = "en_US.UTF-8";
    LC_TIME = "en_US.UTF-8";
  };

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
    # If you want to use JACK applications, uncomment this
    #jack.enable = true;

    # use the example session manager (no others are packaged yet so this is enabled by default,
    # no need to redefine it in your config for now)
    #media-session.enable = true;
  };

  # Enable touchpad support (enabled default in most desktopManager).
  # services.xserver.libinput.enable = true;

  # Define a user account. Don't forget to set a password with ‘passwd’.
  users.users."tetibear" = {
    isNormalUser = true;
    shell = pkgs.zsh;
    description = "tetibear";
    extraGroups = [ "networkmanager" "wheel" ];
    packages = with pkgs; [
    #  thunderbird
    ];
  };

  # Install firefox.
  programs.firefox.enable = true;
  programs.nix-ld.enable = true;

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # List packages installed in system profile. To search, run:
  # $ nix search wget
  environment.systemPackages = with pkgs; [
    vim # Do not forget to add an editor to edit configuration.nix! The Nano editor is also installed by default.
    google-chrome
    claude-code
    wl-clipboard
    git
    discord
    spotify
    fastfetch
    steam
    blender
    tldr
    prismlauncher
    mosh
    jre8
    (python3.withPackages (ps: with ps; [ pip ]))
    vscode
    codex
    obs-studio
    kitty
    waybar
    wofi
    hyprpaper
    hyprlock
    tmux
    rclone
  #  wget
  ];

  programs.tmux = {
    enable = true;
    extraConfig = ''
      source "${pkgs.python3Packages.powerline}/share/tmux/powerline.conf"
    '';
  };

  programs.zsh = {
    enable = true;
    ohMyZsh = {
      enable = true;
      theme = "kawaii";
      plugins = [ "git" "z" ];
      custom = toString (pkgs.runCommand "oh-my-zsh-custom" {} ''
        mkdir -p $out/themes
        cp ${./kawaii.zsh-theme} $out/themes/kawaii.zsh-theme
      '');
    };
  };

  programs.git = {
    enable = true;
    config = {
      user.name = "tetibear-ish";
      user.email = "tetibear@a2z-technologies.com";
    };
  };

  # Some programs need SUID wrappers, can be configured further or are
  # started in user sessions.
  # programs.mtr.enable = true;
  # programs.gnupg.agent = {
  #   enable = true;
  #   enableSSHSupport = true;
  # };

  # List services that you want to enable:

  services.vikunja = {
    enable = true;
    frontendScheme = "http";
    frontendHostname = "hoshimi.taila2fcf3.ts.net";
  };

  services.openssh.enable = true;

  services.tailscale.enable = true;

  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  systemd.services.nixos-upgrade-on-boot = {
    description = "Upgrade NixOS config from GitHub on boot";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      TimeoutStartSec = 300;
    };
    path = with pkgs; [ nixos-rebuild nix git ];
    script = ''
      nixos-rebuild switch --flake github:tetibear-ish/nixos-config --no-write-lock-file
    '';
  };
  networking.firewall.trustedInterfaces = [ "tailscale0" ];

  # Two-way sync between the "IT Wizards" Google Drive folder and the
  # local project's docs/ directory.
  systemd.services."itwizards-drive-bisync" = {
    description = "Bisync IT Wizards Google Drive folder with local docs/";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      User = "tetibear";
      Environment = "HOME=/home/tetibear";
      WorkingDirectory = "/home/tetibear/Projects/IT Wizards";
    };
    path = [ pkgs.rclone ];
    script = ''
      rclone bisync "gdrive:IT Wizards" "/home/tetibear/Projects/IT Wizards/docs" \
        --conflict-resolve newer \
        -v
    '';
  };

  systemd.timers."itwizards-drive-bisync" = {
    description = "Run itwizards-drive-bisync every 5 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "5m";
      Unit = "itwizards-drive-bisync.service";
    };
  };

  # Never sleep or hibernate; only the monitor should turn off
  services.logind.settings.Login = {
    IdleAction = "ignore";
    HandleSuspendKey = "ignore";
    HandleHibernateKey = "ignore";
    HandleLidSwitch = "ignore";
  };
  systemd.sleep.settings.Sleep = {
    AllowSuspend = false;
    AllowHibernation = false;
    AllowSuspendThenHibernate = false;
    AllowHybridSleep = false;
  };

  # Open ports in the firewall.
  # networking.firewall.allowedTCPPorts = [ ... ];
  networking.firewall.allowedUDPPortRanges = [ { from = 60000; to = 61000; } ];
  # Or disable the firewall altogether.
  # networking.firewall.enable = false;

  # This value determines the NixOS release from which the default
  # settings for stateful data, like file locations and database versions
  # on your system were taken. It‘s perfectly fine and recommended to leave
  # this value at the release version of the first install of this system.
  # Before changing this value read the documentation for this option
  # (e.g. man configuration.nix or on https://nixos.org/nixos/options.html).
  system.stateVersion = "26.05"; # Did you read the comment?

}
