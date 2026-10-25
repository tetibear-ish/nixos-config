# System services tetibear may start/stop/restart without a password, so
# editor tooling (Emacs' `SPC s s` services list) can drive them through
# plain `systemctl` instead of sudo. Other modules append to the list.
{ config, lib, ... }:

{
  options.managedServices = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    example = [ "comfyui" ];
    description = "systemd system units (without .service) tetibear can start/stop/restart via polkit.";
  };

  config = lib.mkIf (config.managedServices != [ ]) {
    security.polkit.extraConfig = ''
      polkit.addRule(function (action, subject) {
        var units = ${builtins.toJSON (map (s: "${s}.service") config.managedServices)};
        if (action.id == "org.freedesktop.systemd1.manage-units" &&
            subject.user == "tetibear" &&
            units.indexOf(action.lookup("unit")) >= 0 &&
            ["start", "stop", "restart"].indexOf(action.lookup("verb")) >= 0) {
          return polkit.Result.YES;
        }
      });
    '';
  };
}
