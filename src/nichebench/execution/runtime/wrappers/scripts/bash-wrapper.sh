#!/bin/sh
set -eu

if [ "$#" -ge 2 ] && [ "$1" = "-c" ]; then
  blocked=0
  for git_cmd in checkout restore reset clean switch commit push pull fetch merge rebase log; do
    case "$2" in
      *"/usr/bin/git $git_cmd"*) blocked=1 ;;
    esac
  done
  if [ "$blocked" -eq 1 ]; then
      printf '%s\n' "Absolute /usr/bin/git mutation/history commands are disabled in this benchmark environment." >&2
      printf '%s\n' "Use read/edit and git diff/status for inspection; do not restore or publish changes." >&2
      exit 126
  fi
fi

if [ -x /bin/bash ]; then
  exec /bin/bash "$@"
fi
exec /bin/sh "$@"
