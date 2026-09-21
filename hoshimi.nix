# hoshimi-only services.
{ ... }:

{
  services.vikunja = {
    enable = true;
    frontendScheme = "http";
    frontendHostname = "hoshimi.taila2fcf3.ts.net";
  };
}
