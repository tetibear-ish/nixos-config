# nixos-only: NZXT Kraken LCD control via liquidctl.
{ pkgs, ... }:

{
  environment.systemPackages = [ pkgs.liquidctl ];

  # liquidctl needs raw USB access to talk to the cooler.
  services.udev.packages = [ pkgs.liquidctl ];
}
