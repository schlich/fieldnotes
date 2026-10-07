{
  description = "A terminal dashboard for coding-agent usage, CI, topics waiting to land, and background tasks";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        fieldnotes = pkgs.callPackage ./package.nix { };
        default = fieldnotes;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (python: [
              python.textual
              python.pytest
            ]))
            pkgs.gh
            pkgs.ruff
          ];
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
          '';
        };
      });

      checks = forAllSystems (
        pkgs:
        let
          fieldnotes = self.packages.${pkgs.stdenv.hostPlatform.system}.fieldnotes;
        in
        {
          # Building the package runs the test suite.
          inherit fieldnotes;
          ruff = pkgs.runCommand "fieldnotes-ruff" { nativeBuildInputs = [ pkgs.ruff ]; } ''
            cd ${fieldnotes.src}
            ruff check --no-cache src tests
            ruff format --check --no-cache src tests
            touch $out
          '';
        }
      );

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
