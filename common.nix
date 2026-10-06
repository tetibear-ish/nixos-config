# Shared base config, imported by desktop.nix and server.nix.
{ config, pkgs, unstable, ... }:

let
  # Animated boot splash. Frames come from ./plymouth-frames (any frame-*.png names;
  # gaps are fine) and are renumbered 0..N-1 in sorted order for the script.
  agnesTachyonTheme = pkgs.runCommand "plymouth-theme-agnes-tachyon" {} ''
    themeDir=$out/share/plymouth/themes/agnes-tachyon
    mkdir -p $themeDir
    n=0
    for f in ${./plymouth-frames}/frame-*.png; do
      cp "$f" "$themeDir/$n.png"
      n=$((n + 1))
    done
    substitute ${./agnes-tachyon.script} $themeDir/agnes-tachyon.script \
      --subst-var-by frameCount "$n" \
      --subst-var-by fps 15
    cat > $themeDir/agnes-tachyon.plymouth << EOF
    [Plymouth Theme]
    Name=Agnes Tachyon
    ModuleName=script

    [script]
    ImageDir=$themeDir
    ScriptFile=$themeDir/agnes-tachyon.script
    EOF
  '';

  flash = pkgs.writeShellApplication {
    name = "flash";
    runtimeInputs = with pkgs; [ util-linux coreutils ];
    text = builtins.readFile ./flash.sh;
  };
  test-iso = pkgs.writeShellApplication {
    name = "test-iso";
    runtimeInputs = with pkgs; [ qemu nix coreutils findutils ];
    text = builtins.readFile ./test-iso.sh;
  };
in
{
  imports = [ ./neovim.nix ];

  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;
  # Skip the generation menu; hold Space while booting to bring it back for a rollback.
  boot.loader.timeout = 0;

  boot.plymouth = {
    enable = true;
    theme = "agnes-tachyon";
    themePackages = [ agnesTachyonTheme ];
  };
  boot.kernelParams = [ "quiet" "splash" ];

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

  # List packages installed in system profile. To search, run:
  # $ nix search wget
  environment.systemPackages = with pkgs; [
    vim # Do not forget to add an editor to edit configuration.nix! The Nano editor is also installed by default.
    git
    fastfetch
    tldr
    mosh
    tmux
    flash
    test-iso
    gradle
    cmake
    dotnetCorePackages.sdk_10_0
  #  wget
  ];

  # Lets dotnet global tools and editor extensions (C# Dev Kit, Rider) find the SDK.
  environment.variables.DOTNET_ROOT = "${pkgs.dotnetCorePackages.sdk_10_0}/share/dotnet";

  programs.zsh = {
    enable = true;
    # ytclip: YouTube section -> PNG frames for a Plymouth theme (see ytclip.zsh).
    interactiveShellInit = ''
      source ${pkgs.replaceVars ./ytclip.zsh {
        ytdlp = "${unstable.yt-dlp}/bin/yt-dlp";
        ffmpeg = "${unstable.ffmpeg-headless}/bin/ffmpeg";
      }}
    '';
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
