#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

git_commit="$(git rev-parse --verify HEAD)"
head_tree="$(git rev-parse --verify 'HEAD^{tree}')"
git_objects="$(git rev-parse --path-format=absolute --git-path objects)"

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/contextwiki-eval-provenance.XXXXXXXX")"
temp_index="${temp_dir}/index"
temp_objects="${temp_dir}/objects"
mkdir -p "$temp_objects"

cleanup() {
  rm -rf -- "$temp_dir"
}
trap cleanup EXIT

export GIT_INDEX_FILE="$temp_index"
export GIT_OBJECT_DIRECTORY="$temp_objects"
if [[ -n "${GIT_ALTERNATE_OBJECT_DIRECTORIES:-}" ]]; then
  export GIT_ALTERNATE_OBJECT_DIRECTORIES="${git_objects}:${GIT_ALTERNATE_OBJECT_DIRECTORIES}"
else
  export GIT_ALTERNATE_OBJECT_DIRECTORIES="$git_objects"
fi

git read-tree HEAD
git add -A -- .

# Generated evaluation artifacts and mutable harness progress cannot identify
# implementation. Restore both metadata areas to HEAD, then re-apply the two
# maintained report inputs. Architecture/integration docs remain fingerprinted.
git reset -q HEAD -- evaluation/reports docs/plan
git add -A -- \
  evaluation/reports/README.md \
  evaluation/reports/ci_thresholds.json
worktree_tree="$(git write-tree)"

git_state="dirty"
if [[ "$worktree_tree" == "$head_tree" ]]; then
  git_state="clean"
fi

printf 'commit=%s;head_tree=%s;worktree_tree=%s;state=%s\n' \
  "$git_commit" "$head_tree" "$worktree_tree" "$git_state"
