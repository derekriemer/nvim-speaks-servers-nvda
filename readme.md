# nvim-speaks NVDA server

This add-on runs a local TCP server inside NVDA for the nvim-speaks Neovim plugin.

The server listens on `127.0.0.1:7533` and accepts newline-delimited JSON protocol
envelopes. It handles the command-based `speech.sequence` protocol and falls back
to `speech.text` when a message does not include a usable sequence. Braille is
intentionally left for future protocol support.

## Build and install

From this directory:

```bash
uv sync
uv run scons
```

Install the generated `nvimSpeaksServer-*.nvda-addon` file in NVDA and restart
NVDA if prompted.

To run the template lint and type-check hooks:

```bash
uv run prek run --all-files
```

## Neovim configuration

Configure nvim-speaks to connect through the TCP bridge:

```lua
require("nvim-speaks").setup({
  command = {
    "/path/to/tcp-bridge/speech-to-tcp",
    "127.0.0.1",
    "7533",
  },
})
```

Press NVDA+shift+s to stop or restart the server.
