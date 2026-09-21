{
  description = "NixOS configurations";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  inputs.nixos-generators.url = "github:nix-community/nixos-generators";
  inputs.nixos-generators.inputs.nixpkgs.follows = "nixpkgs";

  outputs = { self, nixpkgs, nixos-generators }:
    let
      lib = nixpkgs.lib;

      hosts = {
        hoshimi = { profile = ./desktop.nix; extraModules = [ ./hoshimi.nix ./minecraft.nix ]; };
        nixos   = { profile = ./desktop.nix; extraModules = [ ]; };
        deli    = { profile = ./desktop.nix; extraModules = [ ]; };
        # INSTALLER: new hosts appended below this line
      };
    in
    {
      nixosConfigurations = lib.mapAttrs
        (name: h: nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            h.profile
            ./hardware-${name}.nix
            { networking.hostName = name; }
          ] ++ h.extraModules;
        })
        hosts;
    };
}
