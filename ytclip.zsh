# ytclip: cut a section of a YouTube video into numbered PNG frames for a Plymouth theme.
ytclip() {
  emulate -L zsh -o extended_glob
  if (( $# < 3 || $# > 4 )); then
    print -u2 "usage: ytclip <youtube-url> <start> <duration> [out-dir]"
    print -u2 "  start/duration: seconds or [HH:]MM:SS[.ms], e.g. ytclip URL 1:23 4.5"
    print -u2 "  out-dir defaults to ./plymouth-frames"
    print -u2 "  env: YTCLIP_FPS (default 15), YTCLIP_WIDTH (default 960)"
    return 2
  fi
  local url=$1 start=$2 dur=$3 out=${4:-./plymouth-frames}
  local fps=${YTCLIP_FPS:-15} width=${YTCLIP_WIDTH:-960}

  if [[ -d $out && -n $out(#qNF) ]]; then
    print -u2 "ytclip: $out is not empty; pick another out-dir or empty it first"
    return 1
  fi
  mkdir -p -- $out || return

  # Video-only stream URL; audio is useless to Plymouth.
  local src
  src=$(@ytdlp@ -q --no-warnings -f 'bv*[height<=1080]/b' -g -- $url) || {
    print -u2 "ytclip: yt-dlp could not resolve $url"
    return 1
  }
  src=${src%%$'\n'*}  # first line only

  # -ss before -i seeks without downloading everything before the start time.
  @ffmpeg@ -hide_banner -loglevel error -stats \
    -ss $start -i $src -t $dur -an \
    -vf "fps=$fps,scale=$width:-2:flags=lanczos" \
    -compression_level 9 $out/frame-%04d.png || {
    print -u2 "ytclip: ffmpeg failed"
    return 1
  }

  local -a frames=($out/frame-*.png(N))
  if (( ! $#frames )); then
    print -u2 "ytclip: no frames written (is the start time past the end of the video?)"
    return 1
  fi
  print "ytclip: $#frames frames at ${fps} fps, ${width}px wide -> $out ($(du -sh -- $out | cut -f1))"
}
