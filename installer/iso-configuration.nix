# Standalone config for the live installer ISO itself — NOT imported by
# common/desktop/server.nix, which describe install *targets*, not this
# live environment. Built via `nix build .#installer-iso`.
{ pkgs, lib, ... }:

let
  kawaii-installer = pkgs.writeShellApplication {
    name = "kawaii-installer";
    runtimeInputs = with pkgs; [
      gum git nix parted dosfstools e2fsprogs util-linux gnused coreutils
    ];
    # SC2034: false positives on the color vars and kaomoji arrays, which
    # are only referenced indirectly via `local -n` namerefs in random_face.
    excludeShellChecks = [ "SC2034" ];
    text = builtins.readFile ./installer-script.sh;
  };
in
{
  image.fileName = "kawaii-nixos-installer.iso";

  boot.plymouth.enable = true;
  boot.plymouth.theme = "catppuccin-macchiato";
  boot.plymouth.themePackages = [ pkgs.catppuccin-plymouth ];

  services.getty.autologinUser = lib.mkForce "root";

  environment.systemPackages = [ kawaii-installer ];

  # Autolaunch the installer TUI the moment the autologin shell starts on
  # tty1. KAWAII_INSTALLER_RAN guards against re-exec if the user drops to
  # a nested interactive shell from within the script itself.
  environment.interactiveShellInit = ''
    if [ "$(tty)" = "/dev/tty1" ] && [ -z "''${KAWAII_INSTALLER_RAN:-}" ]; then
      export KAWAII_INSTALLER_RAN=1
      ${kawaii-installer}/bin/kawaii-installer
      exec bash
    fi
  '';
}
