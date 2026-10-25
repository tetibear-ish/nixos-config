# hoshimi-only services.
{ ... }:

{
  services.vikunja = {
    enable = true;
    frontendScheme = "http";
    frontendHostname = "hoshimi.taila2fcf3.ts.net";
  };

  # No monitors attached: lets GUI apps (e.g. Unity Hub sign-in) show on the
  # client via `ssh -X`.
  services.openssh.settings.X11Forwarding = true;
}
