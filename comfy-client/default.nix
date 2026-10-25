# `import comfy` + the `comfy` CLI; see comfy.py. Added to the system python3
# in desktop.nix.
{ buildPythonPackage, setuptools }:

buildPythonPackage {
  pname = "comfy-client";
  version = "0.1.0";
  pyproject = true;
  src = ./.;
  build-system = [ setuptools ];
  pythonImportsCheck = [ "comfy" ];
}
