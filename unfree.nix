# Proprietary/unfree software, kept out of desktop.nix/server.nix so a
# brand-new install (via the installer) stays free-software-only. Add this
# to a host's extraModules in flake.nix (or import it manually) once you
# want the proprietary apps on that machine.
{ config, pkgs, ... }:

{
  nixpkgs.config.allowUnfree = true;

  environment.systemPackages = with pkgs; [
    claude-code
    google-chrome
    discord
    spotify
    vscode
    (unityhub.overrideAttrs (old: {
      # Unity Editor's bundled UnityShaderCompiler needs libtinfo.so.6 or
      # asset import hangs/crashes. nixpkgs' ncurses only ships libtinfo.so.6
      # as a filename symlink to libncursesw.so.6 (its real SONAME), so
      # ldconfig's cache never indexes it under the "libtinfo.so.6" name and
      # the FHS sandbox's cache-based lookup fails to find it even when the
      # package is present. Pointing LD_LIBRARY_PATH straight at ncurses
      # does a filename-based lookup instead, which finds it.
      postInstall = (old.postInstall or "") + ''
        wrapProgram $out/opt/unityhub/unityhub \
          --prefix LD_LIBRARY_PATH : ${ncurses}/lib
      '';
    }))
  ];

  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
  };
}
