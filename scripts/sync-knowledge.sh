#!/usr/bin/env bash
# ==============================================================================
# sync-knowledge.sh —— 把 knowledge/ 同步到部署机，并给 config.yaml 补条目
#
# 背景：make-persona.py 只管**人格**（persona/sparxie-qq.md → DB）。
#       知识库（knowledge/*.md → /opt/astrbot/data/sparxie_*.md）一直没有脚本，
#       靠手工 cp；而 config.yaml 的 knowledge 条目还要**手工插** ——
#       漏一次就是「文件在、但查不到」的静默失败。
#
# 这个脚本把那两步一起做掉，并在写盘前做检查。
#
# 用法（在**部署机**上跑，或本地带 --remote）：
#   ./sync-knowledge.sh --dry-run              # 只看会做什么，不写盘
#   ./sync-knowledge.sh --local                # 在部署机上直接同步本机文件
#   ./sync-knowledge.sh --host srv --remote    # 从本地推到 srv（走 scp/ssh）
#   ./sync-knowledge.sh --local --yes          # 真做（含插 config）
#   ./sync-knowledge.sh --local --yes --restart
#
# 设计要点（都是踩过的坑）：
#   1. **不用 yaml.safe_dump** —— 它会把 config.yaml 的注释全丢掉（曾丢 52 行）。
#      这里只做**文本外科手术**：按锚点插入，插完数注释行数对不对得上。
#   2. **校验必须真的分支** —— 只 print 校验结果然后照样覆盖，等于没校验。
#      这里 YAML 解析失败就 exit 1。
#   3. **两段式** —— 先把 config 改到临时文件、验过、再原子替换。
# ==============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
KNOWLEDGE_DIR="$REPO/knowledge"

# --- 部署侧路径（可按需覆盖） ---
DATA_DIR="${DATA_DIR:-/opt/astrbot/data}"
CONFIG="${CONFIG:-/opt/astrbot/data/plugin_data/astrbot_plugin_mindscape/config.yaml}"
CONTAINER="${CONTAINER:-qqbot-astrbot}"

HOST=""
REMOTE=0
DRY=0
YES=0
RESTART=0

die() { echo "ERROR: $*" >&2; exit 1; }
note() { printf '  %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --yes|-y)  YES=1 ;;
    --restart) RESTART=1 ;;
    --remote)  REMOTE=1 ;;
    --host)    shift; HOST="${1:-}" ;;
    --local)   REMOTE=0 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

[ -d "$KNOWLEDGE_DIR" ] || die "knowledge/ not found: $KNOWLEDGE_DIR"
[ -n "$HOST" ] && REMOTE=1

# --- 在远端跑，还是本机跑 ---
run_remote() { ssh "$HOST" "$@"; }
sh_() { if [ "$REMOTE" = 1 ]; then run_remote "$@"; else bash -c "$*"; fi; }

echo "=== sync-knowledge ==="
echo "  repo      : $REPO"
echo "  knowledge : $KNOWLEDGE_DIR"
if [ "$REMOTE" = 1 ]; then echo "  目标      : $HOST:$DATA_DIR"; else echo "  目标      : 本机 $DATA_DIR"; fi
echo "  dry-run=$DRY yes=$YES restart=$RESTART"
echo

# ---------------------------------------------------------------------------
# ① 把 knowledge/*.md 同步到 DATA_DIR（文件名 → sparxie_<slug>.md）
#    映射表与 config.yaml 里的既有命名保持一致。
# ---------------------------------------------------------------------------
echo "--- ① 同步知识库文件 ---"
declare -A MAP=(
  ["我的PV.md"]="sparxie_videos.md"
  ["关系网.md"]="sparxie_relations.md"
  ["剧情名词.md"]="sparxie_story.md"
  ["版本时事.md"]="sparxie_version_news.md"
  ["战斗常识.md"]="sparxie_combat_basics.md"
  ["战斗设定.md"]="sparxie_combat.md"
  ["当期.md"]="sparxie_current.md"
)

MISSING_MAP=()
for f in "$KNOWLEDGE_DIR"/*.md; do
  b="$(basename "$f")"
  if [ -z "${MAP[$b]:-}" ]; then MISSING_MAP+=("$b"); continue; fi
  dst="$DATA_DIR/${MAP[$b]}"
  if [ "$DRY" = 1 ]; then
    note "[dry] $b  ->  $dst"
  else
    if [ "$REMOTE" = 1 ]; then
      scp -q "$f" "$HOST:$dst" || die "scp fail: $b"
    else
      cp "$f" "$dst" || die "cp fail: $b"
    fi
    note "$b  ->  $dst  ($(wc -c <"$f") bytes)"
  fi
done
if [ "${#MISSING_MAP[@]}" -gt 0 ]; then
  echo "  NOTE: 以下文件没有映射，未同步（要同步就加进 MAP）:"
  printf '        %s\n' "${MISSING_MAP[@]}"
fi
echo

# ---------------------------------------------------------------------------
# ② 确保 config.yaml 的每个 knowledge 段都有这些条目
#    用 path 行当锚点插入；插完检查 YAML 能解析 + 注释行数没变。
# ---------------------------------------------------------------------------
echo "--- ② 检查/补齐 config.yaml 的 knowledge 条目 ---"

DRY_PY=$([ "$DRY" = 1 ] && echo 1 || echo 0)
[ "$DRY" = 1 ] && YES=0

# 把一段 python 送到目标机执行（本机或远端）；它负责读→插→验→写
read -r -d '' FIXER <<'PYEOF' || true
import sys, io, os, re, shutil, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
try:
    import yaml
except ImportError:
    print("  ERROR: PyYAML 缺失（部署机上一般有）"); raise SystemExit(1)

CONFIG = os.environ["SYK_CONFIG"]
DRY = os.environ.get("SYK_DRY") == "1"
YES = os.environ.get("SYK_YES") == "1"
DATA_DIR = os.environ["SYK_DATA"]

names = ["战斗数据","人物关系","剧情名词","版本时事","战斗常识","当期","我的PV"]
paths = {
  "战斗数据":"sparxie_combat.md","人物关系":"sparxie_relations.md",
  "剧情名词":"sparxie_story.md","版本时事":"sparxie_version_news.md",
  "战斗常识":"sparxie_combat_basics.md","当期":"sparxie_current.md",
  "我的PV":"sparxie_videos.md",
}
DESC = {
  "我的PV":"我自己拍过/参与过的片子：里面发生了什么、我说了什么、哪些说法别搞反（被问到视频、PV、短片、联动、前瞻时查）",
}

src = open(CONFIG, encoding="utf-8").read()
comments_before = sum(1 for l in src.splitlines() if l.lstrip().startswith("#"))

# 找所有 knowledge: 段的行号
lines = src.splitlines()
seg_starts = [i for i, l in enumerate(lines) if l.strip() == "knowledge:"]
print("  config 里 knowledge 段: %d 处（行 %s）" % (len(seg_starts), [i+1 for i in seg_starts]))
if not seg_starts:
    print("  ERROR: 没找到 knowledge 段"); raise SystemExit(1)

# 每段：缩进 = knowledge 行缩进 + 2；条目名行前缀 "- name: "
ins_blocks = []
for st in seg_starts:
    indent = len(lines[st]) - len(lines[st].lstrip())
    item_pad = " " * (indent + 2)
    body_pad = " " * (indent + 4)
    have = set()
    j = st + 1
    while j < len(lines):
        cur = lines[j]
        if cur.strip() and (len(cur) - len(cur.lstrip())) <= indent:
            break
        m = re.match(r'\s*-\s*name:\s*"?([^"]+?)"?\s*$', cur)
        if m: have.add(m.group(1))
        j += 1
    add = [n for n in names if n not in have]
    for n in add:
        blk = [
          '%s- name: "%s"' % (item_pad, n),
          '%spath: "%s/%s"' % (body_pad, DATA_DIR.rstrip("/"), paths[n]),
        ]
        if n in DESC:
            blk.append('%sdesc: "%s"' % (body_pad, DESC[n]))
        ins_blocks.append((j, blk, n))
    print("    段@L%-4d 缩进=%d 已有=%d 条 → 补 %s" % (st+1, indent, len(have), add or "（无）"))

if not ins_blocks:
    print("  OK: 所有条目都在，无需改动 ✓")
    raise SystemExit(0)

if DRY:
    print("  [dry] 将插入 %d 行 × %d 处" % (sum(len(b[1]) for b in ins_blocks), len(ins_blocks)))
    raise SystemExit(0)

new_lines = list(lines)
for pos, blk, _n in sorted(ins_blocks, key=lambda x: -x[0]):   # 从后往前插，行号不失效
    new_lines[pos:pos] = blk
new_src = "\n".join(new_lines)
if src.endswith("\n"): new_src += "\n"

# --- 两段式：写到临时文件 → 校验 → 才替换 ---
tmp = CONFIG + ".syk-tmp"
open(tmp, "w", encoding="utf-8").write(new_src)
try:
    yaml.safe_load(open(tmp, encoding="utf-8"))
except Exception as e:
    os.remove(tmp)
    print("  ERROR: 新 config YAML 解析失败，已中止（原文件未动）:", e); raise SystemExit(1)
comments_after = sum(1 for l in new_src.splitlines() if l.lstrip().startswith("#"))
if comments_after != comments_before:
    os.remove(tmp)
    print("  ERROR: 注释行数变了（%d → %d），已中止" % (comments_before, comments_after)); raise SystemExit(1)
if not YES:
    os.remove(tmp)
    print("  NOTE: 校验通过，但没给 --yes ⇒ 未写盘（去掉 --dry-run 加 --yes 才真做）")
    raise SystemExit(0)
bak = CONFIG + ".bak-syk-" + time.strftime("%Y%m%d%H%M%S")
shutil.copy2(CONFIG, bak)
os.replace(tmp, CONFIG)
print("  ✓ 已写 config（+%d 行 / 注释 %d→%d 不变）备份: %s"
      % (len(new_src.splitlines()) - len(lines), comments_before, comments_after, bak))
print("  md5:", hashlib.md5(open(CONFIG,"rb").read()).hexdigest())
PYEOF

if [ "$REMOTE" = 1 ]; then
  # 远端跑：把 fixer 传过去再跑
  TMPFIX="$(mktemp)"; printf '%s' "$FIXER" > "$TMPFIX"
  scp -q "$TMPFIX" "$HOST:/tmp/syk-fixer.py"; rm -f "$TMPFIX"
  run_remote "SYK_CONFIG='$CONFIG' SYK_DATA='$DATA_DIR' SYK_DRY='$DRY_PY' SYK_YES='$YES' python3 /tmp/syk-fixer.py"
else
  printf '%s' "$FIXER" > /tmp/syk-fixer.py
  SYK_CONFIG="$CONFIG" SYK_DATA="$DATA_DIR" SYK_DRY="$DRY_PY" SYK_YES="$YES" python3 /tmp/syk-fixer.py
fi
echo

# ---------------------------------------------------------------------------
# ③ 端到端：每个 knowledge 条目的文件真的存在吗
# ---------------------------------------------------------------------------
echo "--- ③ 端到端检查：config 里每条 knowledge 的文件是否存在 ---"
read -r -d '' E2E <<'PYEOF' || true
import sys, os, yaml
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
d = yaml.safe_load(open(os.environ["SYK_CONFIG"], encoding="utf-8"))
bad = 0
for b in d.get("memory", {}).get("bots", []):
    sid = b.get("self_id"); kn = b.get("knowledge") or []
    if not kn: 
        print("  %-14s 0 条（未配知识库）" % sid); continue
    print("  %-14s %d 条" % (sid, len(kn)))
    for k in kn:
        p = k.get("path", "")
        ok = os.path.exists(p)
        if not ok: bad += 1
        print("     %-10s %s  %s" % (k.get("name"), p, "✓" if ok else "✗ 不存在"))
if bad:
    print("  ERROR: %d 个路径不存在 ✗" % bad); raise SystemExit(1)
print("  OK: 全部存在 ✓")
PYEOF

if [ "$REMOTE" = 1 ]; then
  TMPE="$(mktemp)"; printf '%s' "$E2E" > "$TMPE"
  scp -q "$TMPE" "$HOST:/tmp/syk-e2e.py"; rm -f "$TMPE"
  run_remote "SYK_CONFIG='$CONFIG' python3 /tmp/syk-e2e.py"
else
  printf '%s' "$E2E" > /tmp/syk-e2e.py
  SYK_CONFIG="$CONFIG" python3 /tmp/syk-e2e.py
fi
echo

# ---------------------------------------------------------------------------
# ④ 可选重启
# ---------------------------------------------------------------------------
if [ "$RESTART" = 1 ]; then
  echo "--- ④ 重启 $CONTAINER（约 30 秒掉线）---"
  if [ "$DRY" = 1 ]; then
    note "[dry] 不重启"
  else
    if [ "$REMOTE" = 1 ]; then run_remote "docker restart $CONTAINER"; else docker restart "$CONTAINER"; fi
    note "重启完成 —— ⚠️ 记得**两个 bot 都查**（不要只查一个）"
    note "  docker logs --since 5m sparxie-snowluma | wc -l"
    note "  docker logs --since 5m qqbot-snowluma  | wc -l"
    note "  ss -tnp | grep -E ':(8888|6199)'"
  fi
fi

echo "=== 完成 ==="
