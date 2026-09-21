# Server profile: CLI-only, no display manager or GUI apps.
{ config, pkgs, ... }:

{
  imports = [ ./common.nix ];

  # Add server-only services here (nginx, postgres, etc.) as needed.
}
