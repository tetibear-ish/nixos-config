# Shared base config, imported by desktop.nix and server.nix.
{ config, pkgs, ... }:

{
  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # networking.wireless.enable = true;  # Enables wireless support via wpa_supplicant.

  # Configure network proxy if necessary
  # networking.proxy.default = "http://user:password@proxy:port/";
  # networking.proxy.noProxy = "127.0.0.1,localhost,internal.domain";

  # Enable networking
  networking.networkmanager.enable = true;

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

  programs.nix-ld.enable = true;

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # List packages installed in system profile. To search, run:
  # $ nix search wget
  environment.systemPackages = with pkgs; [
    vim # Do not forget to add an editor to edit configuration.nix! The Nano editor is also installed by default.
    claude-code
    git
    fastfetch
    tldr
    mosh
    tmux
    neovim
    ripgrep
    fd
  #  wget
  ];

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

  system.activationScripts.tpm.text = ''
    mkdir -p /home/tetibear/.tmux/plugins
    if [ ! -d /home/tetibear/.tmux/plugins/tpm ]; then
      ${pkgs.git}/bin/git clone https://github.com/tmux-plugins/tpm /home/tetibear/.tmux/plugins/tpm
    fi
    chown -R tetibear:users /home/tetibear/.tmux
    if [ -d /home/tetibear/.tmux/plugins/tmux-powerline/themes ] && [ -f /home/tetibear/.config/tmux-powerline/themes/kawaii.sh ]; then
      ln -sf /home/tetibear/.config/tmux-powerline/themes/kawaii.sh /home/tetibear/.tmux/plugins/tmux-powerline/themes/kawaii.sh
    fi
  '';

  # Some programs need SUID wrappers, can be configured further or are
  # started in user sessions.
  # programs.mtr.enable = true;
  # programs.gnupg.agent = {
  #   enable = true;
  #   enableSSHSupport = true;
  # };

  services.openssh.enable = true;

  services.tailscale.enable = true;

  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  systemd.services.nixos-upgrade-on-boot = {
    description = "Upgrade NixOS config from GitHub on boot";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    restartIfChanged = false;
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      TimeoutStartSec = 300;
    };
    path = with pkgs; [ nixos-rebuild nix git ];
    script = ''
      if systemctl cat nixos-rebuild-switch-to-configuration.service >/dev/null 2>&1; then
        echo "Another nixos-rebuild is already in progress, skipping."
        exit 0
      fi
      nixos-rebuild switch --flake github:tetibear-ish/nixos-config --no-write-lock-file
    '';
  };
  networking.firewall.trustedInterfaces = [ "tailscale0" ];

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
