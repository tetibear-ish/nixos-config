{ config, ... }:

let
  dotfiles = "/home/tetibear/dotfiles";

  # Mirrors ~/dotfiles/run.sh: a real symlink straight into the live
  # dotfiles checkout, so editing a dotfile takes effect immediately
  # without a rebuild. `force` lets this take over the plain symlinks
  # run.sh already created on this machine.
  link = path: {
    source = config.lib.file.mkOutOfStoreSymlink "${dotfiles}/${path}";
    force = true;
  };
in
{
  home.username = "tetibear";
  home.homeDirectory = "/home/tetibear";
  home.stateVersion = "26.05";

  # Trackpad: natural (macOS-style) scrolling.
  # Device identified from /proc/bus/input/devices on deli.
  programs.plasma = {
    enable = true;
    input.touchpads = [
      {
        name = "DELL0B21:00 04F3:3147 Touchpad";
        vendorId = "04f3";
        productId = "3147";
        naturalScroll = true;
      }
    ];
  };

  # Everything run.sh symlinks from ~/dotfiles, now declared here instead.
  # (Microsoft.PowerShell_profile.ps1 is intentionally excluded, same as
  # run.sh -- it's Windows-only and has no meaningful target on Linux.)
  home.file = {
    ".zshrc" = link ".zshrc";
    ".bashrc" = link ".bashrc";
    ".bash_profile" = link ".bash_profile";
    ".profile" = link ".profile";
    ".bash_logout" = link ".bash_logout";
    ".shellrc_common" = link ".shellrc_common";
    ".skhdrc" = link ".skhdrc";
    ".config/sxhkd/sxhkdrc" = link ".config/sxhkd/sxhkdrc";
    ".config/hypr/hyprland.conf" = link ".config/hypr/hyprland.conf";
    ".config/i3/config" = link ".config/i3/config";
    ".dwm/autostart.sh" = link ".dwm/autostart.sh";
    ".emacs.d/early-init.el" = link ".emacs.d/early-init.el";
    ".emacs.d/init.el" = link ".emacs.d/init.el";
    ".inputrc" = link ".inputrc";
    ".tmux.conf" = link ".tmux.conf";
    ".xmodmap" = link ".xmodmap";
    ".yabairc" = link ".yabairc";
    ".vimrc" = link ".vimrc";
    ".config/kitty/kitty.conf" = link ".config/kitty/kitty.conf";
  };
}
