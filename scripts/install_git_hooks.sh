#!/bin/sh
# 安装提交前的敏感信息检查：每次 git commit 前自动运行 scripts/check_sensitive.py，发现问题就阻止提交
set -e
hooks="$(git rev-parse --git-common-dir)/hooks"
mkdir -p "$hooks"
cat > "$hooks/pre-commit" <<'HOOK'
#!/bin/sh
root="$(git rev-parse --show-toplevel)"
python="$root/.venv/bin/python"
[ -x "$python" ] || python="$(git rev-parse --git-common-dir)/../.venv/bin/python"
[ -x "$python" ] || python=python3
exec "$python" "$root/scripts/check_sensitive.py"
HOOK
chmod +x "$hooks/pre-commit"
echo "已安装：$hooks/pre-commit"
