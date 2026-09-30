{ lib, pkgs, ... }:

let
  plugins = with pkgs.vimPlugins; [
    plenary-nvim
    nvim-web-devicons
    (nvim-treesitter.withAllGrammars)
    nvim-lspconfig
    nvim-cmp
    cmp-nvim-lsp
    luasnip
    cmp_luasnip
    friendly-snippets
    cmp-buffer
    cmp-path
    telescope-nvim
    project-nvim
    nvim-tree-lua
    vim-projectionist
    smart-splits-nvim
  ];

  # Build a Lua package.path string covering every plugin's lua/ directory.
  # This is injected at the top of init.vim so require() always finds modules
  # regardless of the packpath/rtp loading order during VIMINIT.
  luaPath = lib.concatMapStrings (p: "${p}/lua/?.lua;${p}/lua/?/init.lua;") plugins;
in
{
  environment.systemPackages = with pkgs; [
    lua-language-server
    nil                   # Nix LSP
    pyright               # Python LSP
    jdt-language-server      # Java LSP
    kotlin-language-server   # Kotlin LSP
    ripgrep               # Telescope live grep
    fd                    # Telescope find files
  ];

  programs.neovim = {
    enable        = true;
    defaultEditor = true;
    viAlias       = true;
    vimAlias      = true;

    configure = {
      packages.myPlugins.start = plugins;

      # The actual keymaps/settings/plugin config live in ~/dotfiles (symlinked to
      # ~/.config/nvim/init.lua by dotfiles/run.sh), so editing them just needs a
      # Neovim restart -- not a full NixOS rebuild. Only the plugin list above,
      # and this package.path shim (which needs Nix-computed store paths), belong here.
      customRC = ''
        lua package.path = '${luaPath}' .. package.path
        luafile ~/.config/nvim/init.lua
      '';
    };
  };
}
