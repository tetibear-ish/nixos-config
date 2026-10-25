# Official ChatGPT desktop app for Linux (public preview), repackaged from
# OpenAI's .deb. nixpkgs' `chatgpt` is the macOS build only.
#
# It's a Chromium/Electron app that also bundles its own node, codex, rg and
# tectonic binaries, so it runs inside an FHS env instead of being patchelf'd.
#
# To update: the latest version and SHA256 are in
# https://persistent.oaistatic.com/codex-app-prod/linux/deb/dists/stable/main/binary-amd64/Packages
{ stdenv, fetchurl, dpkg, buildFHSEnv, writeShellScript }:

let
  version = "26.1007.21434";

  unwrapped = stdenv.mkDerivation {
    pname = "chatgpt-desktop-unwrapped";
    inherit version;

    src = fetchurl {
      url = "https://persistent.oaistatic.com/codex-app-prod/linux/deb/pool/main/c/chatgpt/chatgpt_${version}_amd64.deb";
      sha256 = "276b5341c3d74b261d3ad52106f7fa7edde06841e4767fe2a40cb54099b1f3ed";
    };

    nativeBuildInputs = [ dpkg ];
    unpackPhase = "dpkg-deb -x $src .";

    # Leave OpenAI's binaries exactly as shipped; the FHS env provides the libs.
    dontConfigure = true;
    dontBuild = true;
    dontFixup = true;

    installPhase = ''
      mkdir -p $out
      cp -r usr/lib usr/share $out/
      rm -rf $out/share/doc $out/share/lintian
    '';
  };
in
buildFHSEnv {
  name = "chatgpt";
  inherit version;

  targetPkgs = pkgs: with pkgs; [
    unwrapped
    alsa-lib at-spi2-atk at-spi2-core cairo cups dbus expat gdk-pixbuf glib gtk3
    libdrm libgbm libglvnd libnotify libpulseaudio libsecret libusb1 libxkbcommon
    mesa nspr nss openssl pango systemd tpm2-tss vulkan-loader wayland zlib
    libx11 libxcb libxcomposite libxdamage libxext libxfixes libxrandr libxscrnsaver
    xdg-utils git
  ];

  # A stray LD_LIBRARY_PATH from the calling shell can pull in libs built
  # against a different glibc than the FHS env's, so drop it.
  runScript = writeShellScript "chatgpt-launch" ''
    unset LD_LIBRARY_PATH
    exec ${unwrapped}/lib/chatgpt/ChatGPT "$@"
  '';

  extraInstallCommands = ''
    mkdir -p $out/share
    cp -r ${unwrapped}/share/applications ${unwrapped}/share/pixmaps $out/share/
  '';

  meta.description = "ChatGPT desktop app by OpenAI (Linux preview)";
}
