{
  description = "NixOS configurations";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  inputs.nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.nixos-generators.url = "github:nix-community/nixos-generators";
  inputs.nixos-generators.inputs.nixpkgs.follows = "nixpkgs";
  inputs.nixified-ai.url = "github:nixified-ai/flake";

  outputs = { self, nixpkgs, nixpkgs-unstable, nixos-generators, nixified-ai }:
    let
      lib = nixpkgs.lib;

      hosts = {
        hoshimi = { profile = ./desktop.nix; extraModules = [ ./unfree.nix ./hoshimi.nix ./minecraft.nix ./comfyui.nix nixified-ai.nixosModules.comfyui ]; };
        nixos   = { profile = ./desktop.nix; extraModules = [ ./unfree.nix ./kraken.nix ]; };
        deli    = { profile = ./desktop.nix; extraModules = [ ./unfree.nix ]; };
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
            ({ pkgs, ... }: {
              _module.args.unstable = import nixpkgs-unstable {
                system = "x86_64-linux";
                config.allowUnfree = true;
              };
            })
          ] ++ h.extraModules;
        })
        hosts;

      packages.x86_64-linux.installer-iso = nixos-generators.nixosGenerate {
        system = "x86_64-linux";
        format = "install-iso";
        modules = [ ./installer/iso-configuration.nix ];
      };
    };
}
