#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: route.sh --work-class <security-critical|core-backend|frontend|rust-core|tests-docs|lint> --role <dev|review|tiebreak>
USAGE
}

work_class=""
role=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work-class)
      work_class="${2:-}"
      shift 2
      ;;
    --role)
      role="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$work_class" || -z "$role" ]]; then
  usage >&2
  exit 2
fi

model=""
command_template=""

case "$work_class:$role" in
  security-critical:dev)
    model="opus"
    command_template="claude --model opus"
    ;;
  security-critical:review)
    model="deepseek+gemini"
    command_template="scripts/v2/review-deepseek.sh <base>..HEAD && scripts/v2/review-gemini.sh <base>..HEAD"
    ;;
  security-critical:tiebreak)
    model="gemini"
    command_template="scripts/v2/review-gemini.sh <base>..HEAD --checklist 'tiebreak adjudication'"
    ;;
  core-backend:dev)
    model="codex"
    command_template="codex"
    ;;
  core-backend:review)
    model="deepseek"
    command_template="scripts/v2/review-deepseek.sh <base>..HEAD"
    ;;
  core-backend:tiebreak)
    model="gemini"
    command_template="scripts/v2/review-gemini.sh <base>..HEAD --checklist 'tiebreak adjudication'"
    ;;
  frontend:dev)
    model="codex"
    command_template="codex"
    ;;
  frontend:review)
    model="gemini"
    command_template="scripts/v2/review-gemini.sh <base>..HEAD"
    ;;
  frontend:tiebreak)
    model="gemini"
    command_template="scripts/v2/review-gemini.sh <base>..HEAD --checklist 'tiebreak adjudication'"
    ;;
  rust-core:dev)
    model="codex"
    command_template="codex"
    ;;
  rust-core:review)
    model="deepseek+gemini"
    command_template="scripts/v2/review-deepseek.sh <base>..HEAD && scripts/v2/review-gemini.sh <base>..HEAD"
    ;;
  rust-core:tiebreak)
    model="gemini"
    command_template="scripts/v2/review-gemini.sh <base>..HEAD --checklist 'tiebreak adjudication'"
    ;;
  tests-docs:dev)
    model="haiku"
    command_template="claude --model haiku"
    ;;
  tests-docs:review)
    model="codex-spot"
    command_template="codex review --spot"
    ;;
  tests-docs:tiebreak)
    model="gemini"
    command_template="scripts/v2/review-gemini.sh <base>..HEAD --checklist 'tiebreak adjudication'"
    ;;
  lint:dev)
    model="ollama"
    command_template="ollama run <model>"
    ;;
  lint:review|lint:tiebreak)
    echo "unsupported role for lint: $role" >&2
    exit 2
    ;;
  *)
    echo "unsupported route: work-class=$work_class role=$role" >&2
    usage >&2
    exit 2
    ;;
esac

printf 'model=%s\ncommand_template=%s\n' "$model" "$command_template"
