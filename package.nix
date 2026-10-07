{
  lib,
  python3Packages,
  gh,
  # Commands the panels read from, put on the wrapper's PATH. ai-usagebar and
  # iwe are not in nixpkgs, so a consumer adds them with `.override`.
  runtimeTools ? [ gh ],
}:

let
  project = (lib.importTOML ./pyproject.toml).project;
in
python3Packages.buildPythonApplication {
  pname = "fieldnotes";
  inherit (project) version;
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.difference (lib.fileset.unions [
      ./pyproject.toml
      ./README.md
      ./LICENSE
      ./src
      ./tests
    ]) (lib.fileset.fileFilter (file: file.hasExt "pyc") ./.);
  };

  build-system = [ python3Packages.hatchling ];
  dependencies = [ python3Packages.textual ];

  # The tests drive the whole app headless, with canned data.
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pythonImportsCheck = [ "fieldnotes" ];

  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath runtimeTools)
  ];

  meta = {
    inherit (project) description;
    homepage = "https://github.com/schlich/fieldnotes";
    license = lib.licenses.mit;
    mainProgram = "fieldnotes";
    platforms = lib.platforms.linux;
  };
}
