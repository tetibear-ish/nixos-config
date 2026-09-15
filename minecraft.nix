{ lib, pkgs, ... }:

{
  services.minecraft-server = {
    enable = true;
    eula = true;
    declarative = true;
    openFirewall = false; # tailscale0 is already a trusted interface
    package = pkgs.papermcServers.papermc-1_21_11;

    # Aikar's flags — tuned for G1GC, good default for Paper
    jvmOpts = lib.concatStringsSep " " [
      "-Xms4G" "-Xmx8G"
      "-XX:+UseG1GC"
      "-XX:+ParallelRefProcEnabled"
      "-XX:MaxGCPauseMillis=200"
      "-XX:+UnlockExperimentalVMOptions"
      "-XX:+DisableExplicitGC"
      "-XX:+AlwaysPreTouch"
      "-XX:G1NewSizePercent=30"
      "-XX:G1MaxNewSizePercent=40"
      "-XX:G1HeapRegionSize=8M"
      "-XX:G1ReservePercent=20"
      "-XX:G1HeapWastePercent=5"
      "-XX:G1MixedGCCountTarget=4"
      "-XX:InitiatingHeapOccupancyPercent=15"
      "-XX:G1MixedGCLiveThresholdPercent=90"
      "-XX:G1RSetUpdatingPauseTimePercent=5"
      "-XX:SurvivorRatio=32"
      "-XX:+PerfDisableSharedMem"
      "-XX:MaxTenuringThreshold=1"
    ];

    serverProperties = {
      server-port = 25565;
      difficulty = "normal";
      gamemode = "survival";
      max-players = 20;
      motd = "hoshimi";
      online-mode = true;
      enable-command-block = false;
      level-seed = "";
    };
  };
}
