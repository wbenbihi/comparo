"""Shell adapters combining generated static completion with offline project names."""

from __future__ import annotations

MARKER = "# comparo managed completion v1"

_ZSH = r"""
_comparo() {
  local -a suggestions
  suggestions=("${(@f)$(command comparo _complete -- "${words[@]:0:$CURRENT}" 2>/dev/null)}")
  if (( ${#suggestions} )) && [[ -n "${suggestions[1]}" ]]; then
    [[ "${words[CURRENT]}" == --*=* ]] && compset -P '*='
    compadd -- "${suggestions[@]}"
    return
  fi
  _cyclopts_comparo "$@"
}
if (( $+functions[compdef] )); then
  compdef _comparo comparo
fi
"""

_BASH = r"""
_comparo() {
  local item current prefix
  current="${COMP_WORDS[COMP_CWORD]}"
  prefix=''
  [[ "$current" == --*=* ]] && prefix="${current%%=*}="
  COMPREPLY=()
  while IFS= read -r item; do
    [[ -n "$item" ]] && COMPREPLY+=("${prefix}${item}")
  done < <(command comparo _complete -- "${COMP_WORDS[@]:0:$((COMP_CWORD + 1))}" 2>/dev/null)
  if ((${#COMPREPLY[@]})); then
    return
  fi
  _comparo_static
}
complete -F _comparo comparo
"""

_FISH = r"""
function __comparo_project_candidates
    set -l current (commandline -ct)
    command comparo _complete -- (commandline -opc) "$current" 2>/dev/null
end
complete -c comparo -a '(__comparo_project_candidates)'
"""


def completion_script(shell: str, generated: str) -> str:
    """Add a self-registering project-aware wrapper to a framework-generated script."""
    if shell == "bash":
        generated = generated.replace("_comparo() {", "_comparo_static() {", 1)
        suffix = _BASH
    elif shell == "zsh":
        suffix = _ZSH
    elif shell == "fish":
        suffix = (
            _FISH
            + "\n".join(
                f"complete -c comparo -l {option} -r -f "
                "-n '__fish_seen_subcommand_from run diff exec plan render' "
                "-a '(__comparo_project_candidates)'"
                for option in ("environment", "env", "baseline", "candidate", "subject", "profile")
            )
            + "\n"
        )
    else:
        raise ValueError("unsupported completion shell")
    return MARKER + "\n" + generated + suffix
