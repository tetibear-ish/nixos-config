# Proprietary/unfree software, kept out of desktop.nix/server.nix so a
# brand-new install (via the installer) stays free-software-only. Add this
# to a host's extraModules in flake.nix (or import it manually) once you
# want the proprietary apps on that machine.
{ config, pkgs, unstable, ... }:

let
  # Unity editors installed by Hub are generic Linux binaries; they only find
  # their libraries inside Hub's FHS sandbox. Runs one there, picking the
  # version from $UNITY_VERSION, else the project's ProjectVersion.txt (from
  # -projectPath or the current directory), else the newest installed.
  unity-editor = pkgs.writeShellApplication {
    name = "unity-editor";
    text = ''
      editors="''${UNITY_EDITORS:-$HOME/Unity/Hub/Editor}"
      project=.
      prev=
      for arg in "$@"; do
        [ "$prev" = "-projectPath" ] && project="$arg"
        prev="$arg"
      done

      version="''${UNITY_VERSION:-}"
      if [ -z "$version" ] && [ -f "$project/ProjectSettings/ProjectVersion.txt" ]; then
        version=$(sed -n 's/^m_EditorVersion: //p' "$project/ProjectSettings/ProjectVersion.txt")
      fi
      if [ -z "$version" ]; then
        version=$(find "$editors" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort -V | tail -n 1)
      fi

      editor="$editors/$version/Editor/Unity"
      if [ ! -x "$editor" ]; then
        echo "unity-editor: no editor for version '$version' at $editor" >&2
        echo "Install it with: unityhub -- --headless install --version $version" >&2
        exit 1
      fi

      export LD_LIBRARY_PATH="${pkgs.ncurses}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
      exec ${pkgs.unityhub.fhsEnv}/bin/unityhub-fhs-env "$editor" "$@"
    '';
  };
in
{
  nixpkgs.config.allowUnfree = true;

  environment.systemPackages = with pkgs; [
    unstable.claude-code
    # Chrome 153 segfaults at startup with GTK 4 under native Wayland
    # (GTK 4 + X11 and GTK 3 + Wayland both work). Pin it to GTK 3, which
    # keeps it on Wayland. Drop this once a Chrome/GTK update fixes it.
    (google-chrome.override { commandLineArgs = "--gtk-version=3"; })
    (callPackage ./chatgpt-desktop.nix { })   # official Linux preview; see the file for updating
    discord
    spotify
    vscode
    jetbrains.idea   # IntelliJ IDEA (unified; replaces discontinued Community edition)
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
    unity-editor
  ];

  programs.steam = {
    enable = true;
    remotePlay.openFirewall = true;
  };
}
