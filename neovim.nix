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
  ];

  # Build a Lua package.path string covering every plugin's lua/ directory.
  # This is injected at the top of init.vim so require() always finds modules
  # regardless of the packpath/rtp loading order during VIMINIT.
  luaPath = lib.concatMapStrings (p: "${p}/lua/?.lua;${p}/lua/?/init.lua;") plugins;
in
{
  environment.systemPackages = with pkgs; [
    lua-language-server
    nil       # Nix LSP
    pyright   # Python LSP
    ripgrep   # Telescope live grep
    fd        # Telescope find files
  ];

  programs.neovim = {
    enable        = true;
    defaultEditor = true;
    viAlias       = true;
    vimAlias      = true;

    configure = {
      packages.myPlugins.start = plugins;

      customRC = ''
        lua << EOF
        -- Inject plugin lua paths directly so require() works during VIMINIT
        package.path = '${luaPath}' .. package.path

        vim.g.mapleader = ' '

        vim.opt.number         = true
        vim.opt.relativenumber = true
        vim.opt.expandtab      = true
        vim.opt.shiftwidth     = 2
        vim.opt.tabstop        = 2
        vim.opt.smartindent    = true
        vim.opt.wrap           = false
        vim.opt.swapfile       = false
        vim.opt.undofile       = true
        vim.opt.hlsearch       = false
        vim.opt.incsearch      = true
        vim.opt.termguicolors  = true
        vim.opt.scrolloff      = 8
        vim.opt.signcolumn     = 'yes'
        vim.opt.updatetime     = 50
        vim.opt.splitright     = true
        vim.opt.splitbelow     = true

        -- Treesitter (0.10 API: highlight via neovim native, indent via nvim-treesitter)
        vim.api.nvim_create_autocmd('FileType', {
          callback = function(ev)
            pcall(vim.treesitter.start, ev.buf)
            vim.bo[ev.buf].indentexpr = "v:lua.require'nvim-treesitter'.indentexpr()"
          end,
        })

        -- Completion
        local cmp     = require('cmp')
        local luasnip = require('luasnip')
        require('luasnip.loaders.from_vscode').lazy_load()
        cmp.setup {
          snippet = {
            expand = function(args) luasnip.lsp_expand(args.body) end,
          },
          mapping = cmp.mapping.preset.insert {
            ['<C-Space>'] = cmp.mapping.complete(),
            ['<CR>']      = cmp.mapping.confirm { select = true },
            ['<Tab>'] = cmp.mapping(function(fallback)
              if cmp.visible() then cmp.select_next_item()
              elseif luasnip.expand_or_jumpable() then luasnip.expand_or_jump()
              else fallback() end
            end, { 'i', 's' }),
            ['<S-Tab>'] = cmp.mapping(function(fallback)
              if cmp.visible() then cmp.select_prev_item()
              elseif luasnip.jumpable(-1) then luasnip.jump(-1)
              else fallback() end
            end, { 'i', 's' }),
          },
          sources = cmp.config.sources(
            { { name = 'nvim_lsp' }, { name = 'luasnip' } },
            { { name = 'buffer' },   { name = 'path' } }
          ),
        }

        -- LSP (nvim 0.11 native API)
        local caps = require('cmp_nvim_lsp').default_capabilities()
        vim.lsp.config('lua_ls',  { capabilities = caps })
        vim.lsp.config('nil_ls',  { capabilities = caps })
        vim.lsp.config('pyright', { capabilities = caps })
        vim.lsp.enable({ 'lua_ls', 'nil_ls', 'pyright' })

        vim.keymap.set('n', 'gd',         vim.lsp.buf.definition,  { desc = 'Go to definition' })
        vim.keymap.set('n', 'K',          vim.lsp.buf.hover,        { desc = 'Hover docs' })
        vim.keymap.set('n', '<leader>rn', vim.lsp.buf.rename,       { desc = 'Rename symbol' })
        vim.keymap.set('n', '<leader>ca', vim.lsp.buf.code_action,  { desc = 'Code action' })
        vim.keymap.set('n', '[d',         vim.diagnostic.goto_prev, { desc = 'Prev diagnostic' })
        vim.keymap.set('n', ']d',         vim.diagnostic.goto_next, { desc = 'Next diagnostic' })

        -- Telescope
        require('telescope').setup {}
        local b = require('telescope.builtin')
        vim.keymap.set('n', '<leader>ff', b.find_files, { desc = 'Find files' })
        vim.keymap.set('n', '<leader>fg', b.live_grep,  { desc = 'Live grep' })
        vim.keymap.set('n', '<leader>fb', b.buffers,    { desc = 'Buffers' })
        vim.keymap.set('n', '<leader>fh', b.help_tags,  { desc = 'Help tags' })

        -- Projects (project.nvim 4.1.1 — no telescope extension, use manual picker)
        require('project').setup {
          detection_methods = { 'lsp', 'pattern' },
          patterns = { '.git', 'flake.nix', 'Makefile', 'package.json', 'cargo.toml' },
        }
        vim.keymap.set('n', '<leader>pa', function()
          local path = vim.fn.input('Add project: ', vim.fn.getcwd(), 'dir')
          if path ~= '' then
            require('project.core').set_pwd(path, 'manual', vim.api.nvim_get_current_buf())
            vim.notify('Project added: ' .. path)
          end
        end, { desc = 'Add project' })

        vim.keymap.set('n', '<leader>fp', function()
          local recent = require('project').get_recent_projects()
          local pickers  = require('telescope.pickers')
          local finders  = require('telescope.finders')
          local conf     = require('telescope.config').values
          local actions  = require('telescope.actions')
          local state    = require('telescope.actions.state')
          pickers.new({}, {
            prompt_title = 'Projects',
            finder  = finders.new_table { results = recent },
            sorter  = conf.generic_sorter({}),
            attach_mappings = function(prompt_bufnr)
              actions.select_default:replace(function()
                local entry = state.get_selected_entry()
                actions.close(prompt_bufnr)
                if entry then
                  vim.cmd('cd ' .. entry[1])
                  require('telescope.builtin').find_files { cwd = entry[1] }
                end
              end)
              return true
            end,
          }):find()
        end, { desc = 'Projects' })

        -- File tree
        require('nvim-tree').setup {
          view     = { width = 30 },
          renderer = { group_empty = true },
          filters  = { dotfiles = false },
        }
        vim.keymap.set('n', '<leader>e', ':NvimTreeToggle<CR>', { desc = 'Toggle file tree' })
        EOF
      '';
    };
  };
}
