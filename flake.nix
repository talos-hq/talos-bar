{
  description = "talos-bar: cross-platform desktop bar (PySide6 + QML) with Python glue";

  # Same nixpkgs revision as ~/dev/nixos, so Qt here is the Qt KWin runs.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/02c087f3fe673c9e317f206a627cb6b910336dbe";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      qtEnv = [ "QT_PLUGIN_PATH" "QML2_IMPORT_PATH" "QML_IMPORT_PATH" "NIXPKGS_QT6_QML_IMPORT_PATH" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in
    {
      packages = forAll (pkgs: rec {
        talos-bar = pkgs.python3Packages.buildPythonApplication {
          pname = "talos-bar";
          version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.hatchling ];
          # nixpkgs ships PySide6 as one package; the PyPI split name is not provided.
          pythonRemoveDeps = [ "pyside6-essentials" ];
          dependencies = with pkgs.python3Packages; [ pyside6 icalendar markdown-it-py tzlocal ];
          nativeBuildInputs = [ pkgs.qt6.wrapQtAppsHook ];
          buildInputs = [ pkgs.qt6.qtbase pkgs.qt6.qtsvg pkgs.qt6.qtdeclarative pkgs.qt6.qtwayland pkgs.kdePackages.layer-shell-qt ];
          dontWrapQtApps = true;
          # The Plasma session exports its own Qt's QML/plugin paths; a different
          # Qt version loading those aborts QtQuick, so drop them first.
          preFixup = ''
            makeWrapperArgs+=(${pkgs.lib.concatMapStringsSep " " (v: "--unset ${v}") qtEnv})
            makeWrapperArgs+=("''${qtWrapperArgs[@]}")
          '';
          meta.mainProgram = "talos-bar";
        };
        default = talos-bar;
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.pyside6 ps.icalendar ps.markdown-it-py ps.tzlocal ps.pytest ]))
            pkgs.qt6.qtwayland
            pkgs.kdePackages.layer-shell-qt
            pkgs.uv
          ];
          shellHook = ''
            unset ${builtins.concatStringsSep " " qtEnv}
            export QT_PLUGIN_PATH=${pkgs.qt6.qtbase}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtsvg}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.qt6.qtwayland}/${pkgs.qt6.qtbase.qtPluginPrefix}:${pkgs.kdePackages.layer-shell-qt}/${pkgs.qt6.qtbase.qtPluginPrefix}
            export QML_IMPORT_PATH=${pkgs.qt6.qtdeclarative}/${pkgs.qt6.qtbase.qtQmlPrefix}:${pkgs.kdePackages.layer-shell-qt}/${pkgs.qt6.qtbase.qtQmlPrefix}
            export PYTHONPATH=$PWD/src''${PYTHONPATH:+:$PYTHONPATH}
          '';
        };
      });
    };
}
