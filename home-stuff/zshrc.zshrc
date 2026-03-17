autoload -U add-zsh-hook

# PROMPT
function set_prompt_info() {
    # update whether we're in a venv
    if [[ -n "$VIRTUAL_ENV" ]]; then
        PROJECT_INFO=" [${VIRTUAL_ENV:h:t}]"
    else
        PROJECT_INFO=""
    fi
}
add-zsh-hook precmd set_prompt_info
setopt PROMPT_SUBST
PS1='%(?.%F{green}%n@tpu${${(%):-%m}#t1v-n-ab15a7e0-w-} :).%F{red}%n@tpu${${(%):-%m}#t1v-n-ab15a7e0-w-} :( %?)%f %.%F{magenta}${PROJECT_INFO}%f $ '

# default editor = neovim (instead of vim)
alias vim=nvim
export VISUAL=nvim
export EDITOR="$VISUAL"

# git shortcuts
alias gs="git status"

# path stuff
export PATH="$HOME/.local/bin:$PATH"

# shortcut for managing virtual environments
function auto_activate() {
    # only run in interactive shells
    [[ -o interactive ]] || return
    
    # find most proximal "..*/venv" (my venvs are always called venv)
    local dir="$PWD"
    local venv=""
    while [[ "$dir" != "/" ]]; do
        if [[ -d "$dir/venv" && -f "$dir/venv/bin/activate" ]]; then
            venv="$dir/venv"
            break
        fi
        dir="${dir:h}"
    done

    # if the found venv is already active, done
    if [[ "$venv" == "$VIRTUAL_ENV" ]]; then
        return
    fi
    # otherwise, deactivate any current venv and activate any found venv
    if [[ -n "$VIRTUAL_ENV" ]]; then
        deactivate
    fi
    if [[ -n "$venv" ]]; then
        export VIRTUAL_ENV_DISABLE_PROMPT=1
        source "$venv/bin/activate"
    fi
}
# run the function once on shell startup
auto_activate
# run the function every time we change directories thereafter
add-zsh-hook chpwd auto_activate

