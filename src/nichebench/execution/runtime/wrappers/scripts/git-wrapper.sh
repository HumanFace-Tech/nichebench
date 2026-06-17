#!/bin/sh
set -eu

real_git=/usr/bin/git
if [ ! -x "$real_git" ]; then
  real_git=$(command -v git)
fi

subcommand=""
skip_next=0
for arg in "$@"; do
  if [ "$skip_next" -eq 1 ]; then
    skip_next=0
    continue
  fi
  case "$arg" in
    -C|--git-dir|--work-tree|--namespace|--exec-path|--config-env)
      skip_next=1
      ;;
    -c)
      skip_next=1
      ;;
    --paginate|--no-pager|--no-replace-objects|--literal-pathspecs|--glob-pathspecs|--noglob-pathspecs|--icase-pathspecs|--bare)
      ;;
    --version|version)
      exec "$real_git" --version
      ;;
    --*)
      ;;
    -*)
      ;;
    *)
      subcommand="$arg"
      break
      ;;
  esac
done

case "$subcommand" in
  status|diff|rev-parse|show)
    exec "$real_git" "$@"
    ;;
  checkout|restore|reset|clean|switch|commit|push|pull|fetch|merge|rebase|log|reflog|tag|branch)
    printf '%s\n' "git $subcommand is disabled in this benchmark environment." >&2
    printf '%s\n' "Use read/edit and git diff/status for inspection; do not restore or publish changes." >&2
    exit 126
    ;;
  "")
    exec "$real_git" "$@"
    ;;
  *)
    printf '%s\n' "git $subcommand is not allowed in this benchmark environment." >&2
    printf '%s\n' "Allowed git commands: status, diff, rev-parse, show." >&2
    exit 126
    ;;
esac
