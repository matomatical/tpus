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

# default TPU environment variables (zsh doesn't source /etc/profile.d/)
export TPU_CHIPS_PER_PROCESS_BOUNDS=1,1,1
export TPU_PROCESS_BOUNDS=1,1,1
export TPU_VISIBLE_DEVICES=0
export PJRT_DEVICE=TPU
if [[ "$LIBTPU_INIT_ARGS" != *runtime_metric_service_port* ]]; then
    export LIBTPU_INIT_ARGS="${LIBTPU_INIT_ARGS:+$LIBTPU_INIT_ARGS }--runtime_metric_service_port=8431"
fi

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

# colours
export COLORTERM=truecolor
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias fgrep='fgrep --color=auto'
alias egrep='egrep --color=auto'

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion
