{
  description = "NixOS configurations";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }: {
    nixosConfigurations.hoshimi = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./configuration.nix
        ./hardware-hoshimi.nix
        ./minecraft.nix
        { networking.hostName = "hoshimi"; }
      ];
    };

    nixosConfigurations.nixos = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./configuration.nix
        ./hardware-nixos.nix
        { networking.hostName = "nixos"; }
      ];
    };

    nixosConfigurations.deli = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ./configuration.nix
        ./hardware-deli.nix
        { networking.hostName = "deli"; }
      ];
    };
  };
}
