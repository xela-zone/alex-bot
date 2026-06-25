{
  description = "Alex-bot Discord Bot";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        alex-bot = pkgs.callPackage ./nix/package.nix {};
      in
      {
        packages = {
          default = alex-bot;
          inherit alex-bot;
        };

        apps.default = {
          type = "app";
          program = "${alex-bot}/bin/alex-bot";
        };

        devShells.default = pkgs.mkShell {
          packages = [
            alex-bot.pythonEnv
            pkgs.ffmpeg
          ];
        };
      }
    ) // {
      nixosModules.default = import ./nix/module.nix { inherit self; };
    };
}
