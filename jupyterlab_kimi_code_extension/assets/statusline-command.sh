#!/bin/bash
# ============================================================================
# Kimi Code Status Line - Fish Prompt Style (Integrated Left)
# ============================================================================
# Layout: [context %][model][effort][git][env][pwd]
#
# Data sources (kimi 0.31.0; $KIMI_CODE_HOME, default ~/.kimi-code):
#   context % - token sum of the LATEST "usage.record" event in the newest
#               wire.jsonl of the current working directory's sessions
#               (session_index.jsonl maps workDir -> sessionDir) vs the
#               model's max_context_size from config.toml; '?' when unknown
#   model     - display_name (fallback: alias) of default_model from
#               config.toml; '?' when unknown
#   effort    - [thinking] effort from config.toml (shown only when set)
#   git       - git -C "$PWD" branch --show-current (2s timeout)
#   env       - $CONDA_DEFAULT_ENV / $VIRTUAL_ENV
#   pwd       - shortened to the last 3 components
#
# Kimi's [status_line] command contract (tui.toml): the command's first
# stdout line replaces footer line 1, and kimi pipes a JSON snapshot
# (model, cwd, git, usage, mode) on stdin. This script deliberately reads
# the files above instead of the snapshot, so it renders identically inside
# and outside kimi and never blocks on stdin.
#
# Plain ASCII-safe: solid colour blocks, no powerline glyphs. Never fails
# hard: every lookup degrades to a fallback and the script always exits 0.
# ============================================================================

set +e

KIMI_HOME="${KIMI_CODE_HOME:-$HOME/.kimi-code}"
CONFIG_TOML="$KIMI_HOME/config.toml"
INDEX_JSONL="$KIMI_HOME/session_index.jsonl"
DIRTRIM=3

# Color palette
CONTEXT_BG="9977DD"
CONTEXT_FG="000000"
MODEL_BG="774499"
MODEL_FG="FFFFFF"
EFFORT_BG="AA3377"
EFFORT_FG="FFFFFF"
GIT_BG="229922"
GIT_FG="000000"
ENV_BG_CONDA="FFD966"
ENV_BG_VENV="E8E8E8"
ENV_FG="000000"
PWD_BG="12468A"
PWD_FG="E0E0E0"

get_pwd() {
    local pwd_str="${PWD/#$HOME/\~}"
    IFS='/' read -ra parts <<< "$pwd_str"
    local count=${#parts[@]}
    if [ "$count" -gt $((DIRTRIM + 1)) ]; then
        local start="${parts[0]}"
        local end_start=$((count - DIRTRIM))
        local end=("${parts[@]:$end_start}")
        # Join with explicit '/' separators - a ${end[*]} + tr join would
        # turn spaces INSIDE directory names into slashes too.
        local joined="" part
        for part in "${end[@]}"; do
            joined+="/$part"
        done
        echo "$start/...$joined"
    else
        echo "$pwd_str"
    fi
}

get_env() {
    if [ -n "$CONDA_DEFAULT_ENV" ]; then
        echo "conda:$CONDA_DEFAULT_ENV"
    elif [ -n "$VIRTUAL_ENV" ]; then
        echo "venv:$(basename "$VIRTUAL_ENV")"
    fi
}

get_git_branch() {
    local branch
    if command -v timeout >/dev/null 2>&1; then
        branch=$(timeout 2 git -C "$PWD" branch --show-current 2>/dev/null)
    else
        branch=$(git -C "$PWD" branch --show-current 2>/dev/null)
    fi
    [ -n "$branch" ] && echo "$branch"
}

# Extract the quoted value of a "key = value" line from stdin (first match).
_toml_value() {  # $1 = key
    sed -n 's/^[[:space:]]*'"$1"'[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

get_default_model() {
    [ -f "$CONFIG_TOML" ] || return 0
    _toml_value default_model < "$CONFIG_TOML"
}

get_effort() {
    [ -f "$CONFIG_TOML" ] || return 0
    awk '/^\[thinking\]/{f=1;next} /^\[/{f=0} f && /^[[:space:]]*effort[[:space:]]*=/{print;exit}' \
        "$CONFIG_TOML" 2>/dev/null | _toml_value effort
}

# Print the lines of the [models."<alias>"] table, up to the next header.
_model_table() {  # $1 = alias
    [ -f "$CONFIG_TOML" ] || return 0
    awk -v sec="[models.\"$1\"]" 'index($0,sec)==1{f=1;next} /^\[/{f=0} f{print}' \
        "$CONFIG_TOML" 2>/dev/null
}

get_model_display_name() {  # $1 = alias
    _model_table "$1" | _toml_value display_name
}

get_model_max_context() {  # $1 = alias
    _model_table "$1" | sed -n 's/^[[:space:]]*max_context_size[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1
}

# Newest wire.jsonl among the sessions whose workDir is the cwd.
_newest_wire_for_cwd() {
    [ -f "$INDEX_JSONL" ] || return 0
    local newest="" newest_mtime=0 line sdir wire m
    while IFS= read -r line; do
        case "$line" in
            *'"workDir":"'"$PWD"'"'*) ;;
            *) continue ;;
        esac
        sdir=$(printf '%s' "$line" | sed -n 's/.*"sessionDir":"\([^"]*\)".*/\1/p')
        [ -n "$sdir" ] || continue
        for wire in "$sdir"/agents/*/wire.jsonl; do
            [ -f "$wire" ] || continue
            m=$(stat -c %Y "$wire" 2>/dev/null)
            [ -n "$m" ] || continue
            if [ "$m" -gt "$newest_mtime" ]; then
                newest_mtime=$m
                newest="$wire"
            fi
        done
    done < "$INDEX_JSONL"
    [ -n "$newest" ] && echo "$newest"
}

# Extract a numeric field from a usage.record line.
_usage_num() {  # $1 = line, $2 = key
    printf '%s' "$1" | sed -n 's/.*"'"$2"'":\([0-9][0-9]*\).*/\1/p'
}

get_context_pct() {
    local wire uline model max_ctx total in_other out cache_read cache_create
    wire=$(_newest_wire_for_cwd)
    [ -n "$wire" ] || { echo "?"; return; }
    # The latest usage.record sits near EOF; a 1 MiB tail window keeps the
    # scan bounded on multi-MB transcripts.
    uline=$(tail -c 1048576 "$wire" 2>/dev/null | grep '"type":"usage.record"' | tail -n 1)
    [ -n "$uline" ] || { echo "?"; return; }
    model=$(printf '%s' "$uline" | sed -n 's/.*"model":"\([^"]*\)".*/\1/p')
    [ -n "$model" ] || { echo "?"; return; }
    max_ctx=$(get_model_max_context "$model")
    case "$max_ctx" in
        ''|*[!0-9]*) echo "?"; return ;;
    esac
    [ "$max_ctx" -gt 0 ] || { echo "?"; return; }
    in_other=$(_usage_num "$uline" inputOther)
    out=$(_usage_num "$uline" output)
    cache_read=$(_usage_num "$uline" inputCacheRead)
    cache_create=$(_usage_num "$uline" inputCacheCreation)
    total=$(( ${in_other:-0} + ${out:-0} + ${cache_read:-0} + ${cache_create:-0} ))
    echo "$(( total * 100 / max_ctx ))%"
}

get_model_label() {
    local alias display
    alias=$(get_default_model)
    [ -n "$alias" ] || { echo "?"; return; }
    display=$(get_model_display_name "$alias")
    if [ -n "$display" ]; then
        echo "$display"
    else
        echo "${alias##*/}"  # strip the provider prefix ("kimi-code/k3" -> "k3")
    fi
}

# Print one solid segment: " text " on a truecolor background.
print_segment() {  # $1 = text, $2 = bg_hex, $3 = fg_hex
    local text="$1" bg="$2" fg="$3"
    printf "\033[48;2;%d;%d;%dm\033[38;2;%d;%d;%dm %s " \
        $((0x${bg:0:2})) $((0x${bg:2:2})) $((0x${bg:4:2})) \
        $((0x${fg:0:2})) $((0x${fg:2:2})) $((0x${fg:4:2})) \
        "$text"
}

# Build the line left to right: [context][model][effort][git][env][pwd]
prompt=""

# 1. Context percentage ('?' when unknown)
prompt+=$(print_segment "$(get_context_pct)" "$CONTEXT_BG" "$CONTEXT_FG")

# 2. Model
prompt+=$(print_segment "$(get_model_label)" "$MODEL_BG" "$MODEL_FG")

# 3. Effort (only when configured)
effort=$(get_effort)
[ -n "$effort" ] && prompt+=$(print_segment "$effort" "$EFFORT_BG" "$EFFORT_FG")

# 4. Git branch
branch=$(get_git_branch)
[ -n "$branch" ] && prompt+=$(print_segment "$branch" "$GIT_BG" "$GIT_FG")

# 5. Environment (conda/venv)
env_info=$(get_env)
if [ -n "$env_info" ]; then
    env_type="${env_info%%:*}"
    env_name="${env_info##*:}"
    if [ "$env_type" = "venv" ]; then
        env_bg="$ENV_BG_VENV"
    else
        env_bg="$ENV_BG_CONDA"
    fi
    prompt+=$(print_segment "$env_name" "$env_bg" "$ENV_FG")
fi

# 6. PWD (bold)
pwd_str=$(get_pwd)
prompt+=$(printf "\033[48;2;%d;%d;%dm\033[01;38;2;%d;%d;%dm %s " \
    $((0x${PWD_BG:0:2})) $((0x${PWD_BG:2:2})) $((0x${PWD_BG:4:2})) \
    $((0x${PWD_FG:0:2})) $((0x${PWD_FG:2:2})) $((0x${PWD_FG:4:2})) \
    "$pwd_str")

prompt+=$'\033[00m'
echo -n "$prompt"
