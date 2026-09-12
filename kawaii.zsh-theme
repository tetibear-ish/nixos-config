# ─────────────────────────────────────────────
# kawaii.zsh-theme
# terminal creature with feelings
# ─────────────────────────────────────────────

setopt prompt_subst

: ${KAWAII_BEDTIME:="22:30"}
: ${KAWAII_WAKE_TIME:="06:00"}

typeset -ga KAWAII_HAPPY_FACES
typeset -ga KAWAII_ANGRY_FACES
typeset -ga KAWAII_SLEEPY_FACES

KAWAII_HAPPY_FACES=(
  "(˶ᵔ ᵕ ᵔ˶)"
  "(｡•̀ᴗ-)✧"
  "(≧▽≦)"
  "(´꒳\`)"
  "(๑˃ᴗ˂)ﻭ"
)

KAWAII_ANGRY_FACES=(
  "(╬ Ò﹏Ó)"
  "(＃\`Д\`)"
  "( \` ω ´ )"
  "٩(ఠ益ఠ)۶"
  "(¬_¬)"
)

KAWAII_SLEEPY_FACES=(
  "(－ω－) zzZ"
  "(＿ ＿*) Z z z"
  "(∪｡∪)｡｡｡zzZ"
  "(ᴗ˳ᴗ)"
  "(－_－) zzZ"
)

typeset -g KAWAII_FACE=""
typeset -g KAWAII_SKY=""
typeset -g KAWAII_LAST_STATUS=0
typeset -g KAWAII_LAST_FACE=""


# Convert HH:MM into minutes since midnight.
kawaii_time_minutes() {
  local value="$1"
  local hour="${value%%:*}"
  local minute="${value##*:}"

  print $(( 10#$hour * 60 + 10#$minute ))
}


kawaii_is_sleep_time() {
  local now="$(date +%H:%M)"

  local now_mins=$(kawaii_time_minutes "$now")
  local bedtime_mins=$(kawaii_time_minutes "$KAWAII_BEDTIME")
  local wake_mins=$(kawaii_time_minutes "$KAWAII_WAKE_TIME")

  # Sleep interval crosses midnight:
  #
  #   bedtime ───────── 23:59
  #   00:00 ─────────── wake time
  #
  (( now_mins >= bedtime_mins || now_mins < wake_mins ))
}


kawaii_random_face() {
  local array_name="$1"

  # ${(P)...} performs indirect parameter expansion in zsh.
  local -a faces
  faces=( "${(@P)array_name}" )

  # Exclude whichever face was shown last so we never repeat consecutively.
  local -a candidates
  local f
  for f in "${faces[@]}"; do
    [[ "$f" != "$KAWAII_LAST_FACE" ]] && candidates+=("$f")
  done
  # Fall back to the full list if filtering left nothing (e.g. a
  # single-entry array) so we still return a face instead of crashing.
  (( ${#candidates[@]} == 0 )) && candidates=( "${faces[@]}" )

  local index=$(( RANDOM % ${#candidates[@]} + 1 ))

  # Set REPLY instead of printing: callers must invoke this directly
  # (not via `$(...)`), since command substitution forks a subshell and
  # would silently discard the KAWAII_LAST_FACE update below.
  REPLY="${candidates[$index]}"
  KAWAII_LAST_FACE="$REPLY"
}


kawaii_sky() {
  local hour=$(date +%H)

  hour=$(( 10#$hour ))

  if (( hour >= 6 && hour < 11 )); then
    print "🌅"
  elif (( hour >= 11 && hour < 17 )); then
    print "☀️"
  elif (( hour >= 17 && hour < 20 )); then
    print "🌇"
  else
    print "🌙"
  fi
}


kawaii_update_prompt() {
  local exit_code=$?
  local face

  KAWAII_LAST_STATUS=$exit_code
  KAWAII_SKY="$(kawaii_sky)"

  if (( exit_code != 0 )); then
    kawaii_random_face KAWAII_ANGRY_FACES
    face="$REPLY"
    KAWAII_FACE="%F{red}${face}%f"

  elif kawaii_is_sleep_time; then
    kawaii_random_face KAWAII_SLEEPY_FACES
    face="$REPLY"
    KAWAII_FACE="%F{blue}${face}%f"

  else
    kawaii_random_face KAWAII_HAPPY_FACES
    KAWAII_FACE="$REPLY"
  fi
}


# Put our hook first so it captures the previous command's exit code
# before another precmd hook has an opportunity to overwrite $?.
precmd_functions=(
  kawaii_update_prompt
  ${precmd_functions:#kawaii_update_prompt}
)


# Git styling
ZSH_THEME_GIT_PROMPT_PREFIX="%F{magenta}git:(%f%F{yellow}"
ZSH_THEME_GIT_PROMPT_SUFFIX="%f%F{magenta})%f "
ZSH_THEME_GIT_PROMPT_DIRTY="%F{red} ✗%f"
ZSH_THEME_GIT_PROMPT_CLEAN="%F{green} ✓%f"


# ┌─ 🌅 (˶ᵔ ᵕ ᵔ˶) ~/projects/miori git:(main ✓)
# └─ ❯
PROMPT='
%F{blue}┌─%f ${KAWAII_SKY} ${KAWAII_FACE} %F{cyan}%~%f $(git_prompt_info)
%F{blue}└─%f %F{magenta}❯%f '

