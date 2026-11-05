# Shell completion

Install completion for your shell:

```console
comparo completion install zsh
comparo completion status zsh
```

Bash and Fish are also supported. Omit the shell to use the shell named by `SHELL`;
specify it explicitly when your login shell differs from your current shell.
Restart the shell after installation. The install result prints the relevant paths.

Zsh and Bash installation adds a marked Comparo block to the shell startup file.
Zsh respects `ZDOTDIR`, initializes completion only if `compdef` is unavailable,
and works with Oh My Zsh. Fish loads its completion file automatically.
Installation respects `XDG_DATA_HOME` and `XDG_CONFIG_HOME` where applicable.
Repeated installation refreshes the script without duplicating startup lines.
Uninstallation removes the managed script and marked block:

```console
comparo completion uninstall zsh
```

`status` checks files and startup configuration; it cannot inspect another running
shell's function table. If status is ready but Tab does not work, restart the shell
and verify `comparo` is available on its `PATH`. Reinstall after upgrading Comparo.

To manage setup yourself, script generation remains available:

```console
comparo completion zsh > comparo.zsh
```

After initializing zsh completion with `autoload -Uz compinit; compinit`, source
that file. It registers itself; no private function name is needed.

Completion suggests commands, options, and choices. It also suggests project
Environments for `--env`, `--environment`, `--baseline`, and `--candidate`, Requests
for `--subject`, and ExecutionProfiles for `--profile` and `exec`. It respects preceding
`--config/-C` and `--source/-S` arguments. It only reads and compiles local configuration;
it does not resolve credentials or send HTTP requests. Missing, invalid, or oversized
projects silently yield no project suggestions. Static completion remains available.
