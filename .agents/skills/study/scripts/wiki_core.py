#!/usr/bin/env python3
"""Core utilities for the 3-Tier Orthogonal LLM Wiki (OKF v0.2 + Grounded Claims).

Provides zero-dependency YAML frontmatter parsing, surgical line-preserving
frontmatter updates (ported from LangChain OpenWiki's repairOkfFrontmatter),
SHA-256 evidence fingerprinting, and card/script discovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TIERS = ("foundations", "tools_craft", "workflows")
CARD_TYPES = ("core-rule", "craft-card", "style-card")
DOC_TYPES = ("concept-doc", "reference-case", "orientation-guide")
ALL_PAGE_TYPES = CARD_TYPES + DOC_TYPES
EPISTEMIC_STATUSES = ("canonical", "provisional", "empirical-refine")

RESERVED_FILENAMES = {
    "INSTRUCTIONS.md",
    "quickstart.md",
    "_domain_index.md",
    "_domain_gotchas.md",
    "_env_and_cli_gotchas.md",
    "index.md",
    "log.md",
    "CURRICULUM_GAPS.md",
}

# Sirchmunk (arXiv:2608.16185) DocumentTree adaptive depth & summary bounds
TREE_ADAPTIVE_DEPTH_THRESHOLDS: tuple[tuple[int, int], ...] = (
    (40_000, 4),
    (15_000, 3),
    (3_000, 2),
)
TOC_NODE_SUMMARY_MAX_CHARS = 240
MAX_PATTERNS_PER_CARD = 5
MAX_CONSTRAINTS_PER_CARD = 4

DEPS_BANNER_START = "<!-- WIKI-DEPS:START -->"
DEPS_BANNER_END = "<!-- WIKI-DEPS:END -->"
MANAGED_START = "<!-- WIKI-MANAGED:START -->"
MANAGED_END = "<!-- WIKI-MANAGED:END -->"


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the repository root containing SCHEMA.md and wiki/."""
    cur = (start or Path.cwd()).resolve()
    script_dir = Path(__file__).resolve().parent
    for candidate in [cur, *cur.parents, script_dir, *script_dir.parents]:
        if (candidate / "SCHEMA.md").exists() and (candidate / "wiki").is_dir():
            return candidate
    return cur


def now_iso() -> str:
    """Return current UTC ISO-8601 timestamp with explicit offset (OKF v0.2)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream a file through SHA-256 (multi-GB lecture videos must never be loaded into RAM at once)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def resolve_cargo() -> str | None:
    """Return the cargo executable every tool must share, or None when Rust is not installed.

    rustup's `~/.cargo/bin/cargo` wins over a distro `/usr/bin/cargo`: old distro toolchains cannot even
    parse a v4 `Cargo.lock`, which made `verify-suite` fail while the linter (using rustup cargo) passed.
    """
    import shutil

    exe = "cargo.exe" if os.name == "nt" else "cargo"
    home_cargo = Path.home() / ".cargo" / "bin" / exe
    if home_cargo.exists():
        return str(home_cargo)
    return shutil.which("cargo")


def rust_subprocess_env() -> dict[str, str]:
    """Environment for cargo subprocesses (pins SDKROOT on macOS Command Line Tools installs)."""
    env = os.environ.copy()
    macos_sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
    if macos_sdk.exists():
        env["SDKROOT"] = str(macos_sdk.resolve())
    return env


def wiki_rust_scripts(wiki_root: Path) -> list[Path]:
    """All Rust scripts that must be registered as Cargo `[[bin]]` targets (single source of truth)."""
    return sorted(
        s for s in wiki_root.rglob("*.rs")
        if "scripts" in s.parts and not s.name.startswith("test_") and "target" not in s.parts
    )


def frontmatter_scalar_problems(raw_frontmatter_lines: list[str], key: str) -> list[str]:
    """Detect YAML shapes that silently corrupt a single-line routing field such as `description`.

    - Block scalars (`description: >-` / `|`) parse to the literal indicator here, and to a multi-line
      string in real YAML parsers; either way the routing key is no longer a single line.
    - An unquoted ` #` starts a YAML comment, so `... 按 #section 点读` is truncated by every YAML parser
      (including this one and Obsidian). The fix is to quote the value.
    """
    problems: list[str] = []
    for line in raw_frontmatter_lines:
        if not line.startswith(f"{key}:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value[:1] in ("|", ">"):
            problems.append(f"'{key}' uses a YAML block scalar ('{value[:2]}'); it must be a single-line value")
        elif value and value[0] not in ("'", '"') and " #" in value:
            problems.append(
                f"'{key}' contains an unquoted ' #', which YAML treats as a comment and truncates; wrap the value in quotes"
            )
    return problems


def write_text_if_changed(path: Path, content: str) -> bool:
    """Write text to `path` ONLY if its byte content actually changed (preserves mtime & Prefix/File Cache)."""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                return False
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def write_json_if_changed(
    path: Path,
    data: dict[str, Any],
    volatile_keys: tuple[str, ...] = ("updatedAt", "lastFinalizedAt"),
) -> bool:
    """Write JSON to `path` ONLY if non-volatile fields changed (prevents timestamp-only churn)."""
    if path.exists():
        try:
            existing_raw = path.read_text(encoding="utf-8")
            existing_data = json.loads(existing_raw)
            if isinstance(existing_data, dict):
                clean_old = {k: v for k, v in existing_data.items() if k not in volatile_keys}
                clean_new = {k: v for k, v in data.items() if k not in volatile_keys}
                if clean_old == clean_new:
                    return False
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True



def strip_deps_banner(body: str) -> str:
    """Remove the auto-injected dependency banner when measuring or hashing body content."""
    pattern = re.compile(
        r"^" + re.escape(DEPS_BANNER_START) + r".*?" + re.escape(DEPS_BANNER_END) + r"\n*",
        re.DOTALL,
    )
    return pattern.sub("", body.lstrip("\n"))


def parse_simple_yaml_value(val_str: str) -> Any:
    """Parse a single-line YAML scalar, inline list, or inline dict."""
    s = val_str.strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [parse_simple_yaml_value(item) for item in _split_top_level(inner, ",")]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, Any] = {}
        for part in _split_top_level(inner, ","):
            if ":" in part:
                k, v = part.split(":", 1)
                result[k.strip()] = parse_simple_yaml_value(v)
        return result
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split string by sep ignoring occurrences inside quotes, brackets, or braces."""
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    in_quote: str | None = None
    for ch in text:
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                cur.append(ch)
            elif ch in ("[", "{"):
                depth += 1
                cur.append(ch)
            elif ch in ("]", "}"):
                depth = max(0, depth - 1)
                cur.append(ch)
            elif ch == sep and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
            else:
                cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


@dataclass
class ParsedMarkdown:
    frontmatter: dict[str, Any]
    raw_frontmatter_lines: list[str]
    body: str
    has_frontmatter: bool


def parse_markdown(content: str) -> ParsedMarkdown:
    """Parse YAML frontmatter and markdown body without external dependencies."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return ParsedMarkdown(frontmatter={}, raw_frontmatter_lines=[], body=content, has_frontmatter=False)

    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return ParsedMarkdown(frontmatter={}, raw_frontmatter_lines=[], body=content, has_frontmatter=False)

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])
    if content.endswith("\n") and not body.endswith("\n"):
        body += "\n"

    fm: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[Any] | None = None

    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Top-level key
        if not line.startswith(" ") and not line.startswith("\t") and ":" in line:
            key, val_part = line.split(":", 1)
            key = key.strip()
            val_part = val_part.strip()
            # Strip inline comments unless inside quotes
            if val_part and not val_part.startswith(('"', "'", "{", "[")) and " #" in val_part:
                val_part = val_part.split(" #", 1)[0].strip()
            if val_part == "":
                current_key = key
                current_list = []
                fm[key] = current_list
            else:
                current_key = None
                current_list = None
                fm[key] = parse_simple_yaml_value(val_part)
            i += 1
            continue

        # Indented list item under current_key
        if current_key is not None and current_list is not None and stripped.startswith("- "):
            item_body = stripped[2:].strip()
            # Check if it's a multi-line dict item in a list (e.g. `- role: generator\n    path: ...`)
            if ":" in item_body and not item_body.startswith(("{", '"', "'")) and not item_body.startswith(("raw://", "script://", "wiki://", "http://", "https://")):
                k1, v1 = item_body.split(":", 1)
                obj: dict[str, Any] = {k1.strip(): parse_simple_yaml_value(v1)}
                i += 1
                while i < len(fm_lines):
                    sub = fm_lines[i]
                    sub_s = sub.strip()
                    if sub.startswith("    ") and not sub_s.startswith("- ") and ":" in sub_s:
                        sk, sv = sub_s.split(":", 1)
                        obj[sk.strip()] = parse_simple_yaml_value(sv)
                        i += 1
                    else:
                        break
                current_list.append(obj)
                continue
            else:
                current_list.append(parse_simple_yaml_value(item_body))
                i += 1
                continue

        i += 1

    return ParsedMarkdown(
        frontmatter=fm,
        raw_frontmatter_lines=fm_lines,
        body=body,
        has_frontmatter=True,
    )


def format_yaml_field(key: str, value: Any) -> list[str]:
    """Serialize a single frontmatter field into clean YAML lines."""
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        lines = [f"{key}:"]
        for item in value:
            if isinstance(item, dict):
                # Inline dict if small (like verified stamp), or multi-line if script entry
                if set(item.keys()) <= {"by", "at"}:
                    by_v = item.get("by", "")
                    at_v = item.get("at", "")
                    lines.append(f'  - {{ by: "{by_v}", at: "{at_v}" }}')
                else:
                    first = True
                    for k, v in item.items():
                        prefix = "  - " if first else "    "
                        lines.append(f"{prefix}{k}: {_format_scalar(v)}")
                        first = False
            else:
                lines.append(f"  - {_format_scalar(item)}")
        return lines
    if isinstance(value, dict):
        parts = [f'{k}: "{v}"' for k, v in value.items()]
        return [f"{key}: {{ {', '.join(parts)} }}"]
    return [f"{key}: {_format_scalar(value)}"]


def _format_scalar(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    s = str(val)
    if not s:
        return '""'
    if any(ch in s for ch in (":", "#", "[", "]", "{", "}")) and not s.startswith(("raw://", "script://", "wiki://")):
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s


def surgical_update_frontmatter(content: str, updates: dict[str, Any]) -> str:
    """Update or insert specific YAML frontmatter keys while preserving other lines and body byte-for-byte."""
    parsed = parse_markdown(content)
    if not parsed.has_frontmatter:
        fm_lines: list[str] = []
        for k, v in updates.items():
            fm_lines.extend(format_yaml_field(k, v))
        return "---\n" + "\n".join(fm_lines) + "\n---\n\n" + content

    # Identify blocks per top-level key in raw_frontmatter_lines
    lines = parsed.raw_frontmatter_lines
    blocks: list[tuple[str | None, list[str]]] = []
    cur_key: str | None = None
    cur_block: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not line.startswith((" ", "\t")) and ":" in line and not stripped.startswith("#"):
            if cur_block:
                blocks.append((cur_key, cur_block))
            cur_key = line.split(":", 1)[0].strip()
            cur_block = [line]
        else:
            cur_block.append(line)
    if cur_block:
        blocks.append((cur_key, cur_block))

    remaining = dict(updates)
    new_fm_lines: list[str] = []
    for k, blk in blocks:
        if k is not None and k in remaining:
            new_Val = remaining.pop(k)
            if new_Val is not None:
                new_fm_lines.extend(format_yaml_field(k, new_Val))
        else:
            new_fm_lines.extend(blk)

    for k, v in remaining.items():
        if v is not None:
            new_fm_lines.extend(format_yaml_field(k, v))

    body_part = parsed.body
    if body_part and not body_part.startswith("\n"):
        body_part = "\n" + body_part
    return "---\n" + "\n".join(new_fm_lines) + "\n---" + body_part


def replace_managed_block(content: str, block_body: str, start_marker: str = MANAGED_START, end_marker: str = MANAGED_END) -> str:
    """Replace content inside <!-- WIKI-MANAGED:START --> ... <!-- WIKI-MANAGED:END -->."""
    replacement = f"{start_marker}\n{block_body.rstrip()}\n{end_marker}"
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    if pattern.search(content):
        return pattern.sub(lambda _: replacement, content)
    return content.rstrip() + "\n\n" + replacement + "\n"


def resolve_evidence_fingerprint(repo_root: Path, resource_uri: str) -> tuple[bool, str]:
    """Resolve a Grounded Claim evidence URI (`raw://`, `script://`, `wiki://`) and compute its SHA-256 fingerprint.

    Returns (exists_and_valid, fingerprint).
    """
    if "://" not in resource_uri:
        return False, ""
    scheme, rest = resource_uri.split("://", 1)
    path_part, _, fragment = rest.partition("#")
    path_part = path_part.lstrip("/")

    if scheme == "raw":
        target = repo_root / "raw" / path_part if not path_part.startswith("raw/") else repo_root / path_part
    elif scheme == "script":
        target = repo_root / path_part if path_part.startswith("wiki/") else repo_root / "wiki" / path_part
    elif scheme == "wiki":
        target = repo_root / path_part if path_part.startswith("wiki/") else repo_root / "wiki" / path_part
    else:
        return False, ""

    if not target.exists() or not target.is_file():
        if scheme == "raw":
            cand_receipts = [target.with_name(target.name + ".receipt.json")]
            base_stem = re.sub(
                r"\.(en\.vtt|zh\.vtt|vtt|srt|mp4|mkv|mov|webm|wav|mp3|flac|pdf|epub)$",
                "",
                target.name,
                flags=re.IGNORECASE,
            )
            if base_stem and target.parent.is_dir():
                cand_receipts.append(target.parent / f"{base_stem}.receipt.json")
                cand_receipts.append(target.parent / f"{base_stem}.mp4.receipt.json")
                for sib in sorted(target.parent.iterdir()):
                    if not sib.is_file():
                        continue
                    sib_stem = re.sub(
                        r"\.(en\.vtt|zh\.vtt|vtt|srt|mp4|mkv|mov|webm|wav|mp3|flac|pdf|epub)?(\.receipt\.json)?$",
                        "",
                        sib.name,
                        flags=re.IGNORECASE,
                    )
                    if sib.name.endswith(".receipt.json") and (
                        sib.name.startswith(base_stem + ".")
                        or sib.name.startswith(base_stem + "_")
                        or (len(sib_stem) >= 8 and base_stem.startswith(sib_stem))
                    ):
                        cand_receipts.append(sib)
                    elif not sib.name.endswith(".receipt.json") and (
                        sib.name.startswith(base_stem + "_")
                        or sib.name.startswith(base_stem + ".")
                        or (len(sib_stem) >= 8 and base_stem.startswith(sib_stem) and sib.suffix.lower() == target.suffix.lower())
                    ):
                        target = sib
                        break
            if not target.exists() or not target.is_file():
                for receipt_path in cand_receipts:
                    if receipt_path.is_file():
                        try:
                            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                            base_hash = str(receipt.get("sha256", ""))
                            if base_hash:
                                if fragment:
                                    return True, sha256_text(f"{base_hash}#{fragment}")
                                return True, base_hash
                        except Exception:
                            pass
        if not target.exists() or not target.is_file():
            return False, ""

    # If line range fragment `#L10-L25` is provided on a text/script file
    line_match = re.fullmatch(r"L(\d+)(?:-L?(\d+))?", fragment)
    if line_match and scheme in ("script", "wiki"):
        start_line = int(line_match.group(1))
        end_line = int(line_match.group(2) or start_line)
        try:
            file_lines = target.read_text(encoding="utf-8").splitlines()
            if start_line < 1 or end_line > len(file_lines) or start_line > end_line:
                return False, ""
            snippet = "\n".join(file_lines[start_line - 1 : end_line])
            return True, sha256_text(snippet)
        except Exception:
            return False, ""

    # For wiki:// markdown references without line ranges, hash clean body (excluding frontmatter timestamps & banners)
    if scheme == "wiki" and target.suffix == ".md":
        parsed_md = parse_markdown(target.read_text(encoding="utf-8"))
        clean_md = strip_deps_banner(parsed_md.body).strip()
        if fragment:
            clean_md += f"#{fragment}"
        return True, sha256_text(clean_md)

    # For binary/multimodal files or whole-file references, hash file bytes + fragment metadata.
    # Raw files are fingerprinted exactly like their `.receipt.json` (whole-file sha256, then `#fragment`),
    # so a claim keeps the same fingerprint before and after `prune-raw` replaces the media with a receipt.
    base_hash = sha256_file(target)
    if scheme == "raw":
        return True, sha256_text(f"{base_hash}#{fragment}") if fragment else base_hash
    if not fragment:
        return True, base_hash
    h = hashlib.sha256()
    with target.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    h.update(f"#{fragment}".encode("utf-8"))
    return True, "sha256:" + h.hexdigest()


def legacy_raw_fragment_fingerprint(repo_root: Path, resource_uri: str) -> str:
    """Fingerprint formula used before v2.1 for an on-disk raw file with a `#fragment` (bytes + '#frag').

    Only used to silently migrate existing claims to the unified receipt-compatible formula.
    """
    if not resource_uri.startswith("raw://") or "#" not in resource_uri:
        return ""
    path_part, _, fragment = resource_uri[len("raw://"):].partition("#")
    path_part = path_part.lstrip("/")
    target = repo_root / path_part if path_part.startswith("raw/") else repo_root / "raw" / path_part
    if not target.is_file():
        return ""
    h = hashlib.sha256()
    with target.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    h.update(f"#{fragment}".encode("utf-8"))
    return "sha256:" + h.hexdigest()


def slugify_heading(title: str) -> str:
    """Deterministic GitHub-style section anchor slugifier."""
    s = title.lower().strip()
    s = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "section"


# Chinese particles / request filler.
# Pure grammatical particles never form 2-char domain root terms ("色的", "的阴", "了吗").
# Polysemous characters like "着", "过", "了" are only filtered as 1-char tokens or when paired with another
# filler/particle char, preserving domain terms like "着色" (shader), "过渡" (transition), "过门" (drum fill),
# "过曝" (overexposure), "过载" (overdrive), "过滤" (filter), and "明了" (clarity).
_CJK_PURE_PARTICLES = set("的吗呢吧啊么")
_CJK_FILLER = set("了着过我你他她它们帮请把给让这那个一些下和与及或在是有要想就也都很")
_CJK_STOP_BIGRAMS = {
    "怎么", "如何", "什么", "一下", "一个", "这个", "那个", "可以", "需要",
    "进行", "时候", "帮忙", "能否", "是否", "看着", "有着", "随着", "通过",
    "经过", "超过", "完了", "好了", "为了", "除了",
}


def is_noise_cjk_token(tok: str) -> bool:
    """True for CJK query tokens that carry no retrieval signal."""
    if not tok or not re.fullmatch(r"[\u4e00-\u9fff]+", tok):
        return False
    if len(tok) == 1:
        return tok in _CJK_PURE_PARTICLES or tok in _CJK_FILLER
    if len(tok) == 2:
        return (
            tok in _CJK_STOP_BIGRAMS
            or any(c in _CJK_PURE_PARTICLES for c in tok)
            or all(c in _CJK_FILLER for c in tok)
        )
    return False


@dataclass
class TreeNode:
    """PageIndex / Sirchmunk (arXiv:2608.16185) document tree node."""

    node_id: str
    slug: str
    title: str
    summary: str
    char_range: tuple[int, int]
    line_range: tuple[int, int]
    level: int = 2
    quant_rule_count: int = 0
    table_count: int = 0
    has_mermaid: bool = False
    children: list["TreeNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodeId": self.node_id,
            "slug": self.slug,
            "title": self.title,
            "summary": self.summary,
            "charRange": list(self.char_range),
            "lineRange": list(self.line_range),
            "level": self.level,
            "quantRuleCount": self.quant_rule_count,
            "tableCount": self.table_count,
            "hasMermaid": self.has_mermaid,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class DocumentTree:
    """PageIndex / Sirchmunk hierarchical tree index for a single Wiki card."""

    page_id: str
    rel_path: str
    body_hash: str
    total_chars: int
    total_lines: int
    max_depth: int
    nodes: list[TreeNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageId": self.page_id,
            "relPath": self.rel_path,
            "bodyHash": self.body_hash,
            "totalChars": self.total_chars,
            "totalLines": self.total_lines,
            "maxDepth": self.max_depth,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    def all_flat_nodes(self) -> list[TreeNode]:
        out: list[TreeNode] = []
        for n in self.nodes:
            out.append(n)
            for c in n.children:
                out.append(c)
                out.extend(c.children)
        return out


_QUANT_RULE_RE = re.compile(
    r"(\d+(\.\d+)?\s*(%|dB|dBFS|Hz|kHz|ms|s|BPM|st|cent|拍|小节|度|音分|行|字|px|pt|g|mg|kcal)|"
    r"[<>≤≥]=?\s*-?\d+|\[\s*-?\d+[^]]*\]|严禁|不得超过|至少|必须)",
    re.IGNORECASE,
)


def _summarize_section_lines(sec_lines: list[str], max_chars: int = TOC_NODE_SUMMARY_MAX_CHARS) -> str:
    """Extract a high-signal <=240 char summary from section lines (bold rule headers + first claims)."""
    bolds: list[str] = []
    plain_snippets: list[str] = []
    in_code = False
    for ln in sec_lines[1:]:
        s = ln.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s or s.startswith(("| ---", "| :---", "<!--", "---")):
            continue
        for m in re.finditer(r"\*\*([^\*\n]{3,75})\*\*", s):
            b = m.group(1).strip().rstrip("：:")
            if b and b not in bolds and not b.startswith("跨层强绑定"):
                bolds.append(b)
        if not bolds and len(plain_snippets) < 2 and not s.startswith(("#", "|")):
            clean_s = re.sub(r"[`*_>#\[\]]+", "", s).strip()
            if len(clean_s) >= 8:
                plain_snippets.append(clean_s)

    if bolds:
        summary = " · ".join(bolds[:5])
    elif plain_snippets:
        summary = " ".join(plain_snippets)
    else:
        summary = "(包含图表/代码/参数矩阵)"
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


def build_document_tree(
    page_id: str,
    rel_path: str,
    clean_body: str,
    body_hash: str,
) -> DocumentTree:
    """Build a Sirchmunk-style hierarchical DocumentTree from a Markdown card's clean_body."""
    text = clean_body.strip()
    lines = text.splitlines()
    total_chars = len(text)
    total_lines = len(lines)

    max_depth = 2
    for min_c, depth in TREE_ADAPTIVE_DEPTH_THRESHOLDS:
        if total_chars >= min_c:
            max_depth = depth
            break

    # Locate all H2 / H3 (and H4 if max_depth >= 4) headings outside fenced code blocks
    raw_headings: list[tuple[int, int, int, str]] = []  # (line_idx_0, char_offset, level, title)
    in_code = False
    char_cursor = 0
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("```"):
            in_code = not in_code
        elif not in_code:
            m = re.match(r"^(#{2,4})\s+(.+)$", s)
            if m:
                lvl = len(m.group(1))
                if lvl <= max(3, max_depth):
                    raw_headings.append((idx, char_cursor, lvl, m.group(2).strip()))
        char_cursor += len(ln) + 1

    if not raw_headings:
        # Single-node fallback for short cards without H2
        q_cnt = sum(1 for ln in lines if _QUANT_RULE_RE.search(ln))
        t_cnt = sum(1 for ln in lines if ln.strip().startswith("| ---"))
        node = TreeNode(
            node_id="N01",
            slug="overview",
            title="Overview",
            summary=_summarize_section_lines(["# Overview", *lines]),
            char_range=(0, total_chars),
            line_range=(1, max(1, total_lines)),
            level=2,
            quant_rule_count=q_cnt,
            table_count=t_cnt,
            has_mermaid="```mermaid" in text,
        )
        return DocumentTree(
            page_id=page_id,
            rel_path=rel_path,
            body_hash=body_hash,
            total_chars=total_chars,
            total_lines=total_lines,
            max_depth=max_depth,
            nodes=[node],
        )

    # First build flat spans for each heading up to the next heading of <= level
    flat_nodes: list[TreeNode] = []
    for i, (l_idx, c_off, lvl, h_title) in enumerate(raw_headings):
        end_l_idx = total_lines
        end_c_off = total_chars
        for j in range(i + 1, len(raw_headings)):
            next_l_idx, next_c_off, next_lvl, _ = raw_headings[j]
            if next_lvl <= lvl:
                end_l_idx = next_l_idx
                end_c_off = max(c_off, next_c_off - 1)
                break
        sec_lines = lines[l_idx:end_l_idx]
        sec_text = "\n".join(sec_lines)
        q_cnt = sum(1 for ln in sec_lines if _QUANT_RULE_RE.search(ln))
        t_cnt = sum(1 for ln in sec_lines if ln.strip().startswith(("| ---", "| :---")))
        flat_nodes.append(
            TreeNode(
                node_id=f"N{i + 1:02d}",
                slug=slugify_heading(h_title),
                title=h_title,
                summary=_summarize_section_lines(sec_lines),
                char_range=(c_off, end_c_off),
                line_range=(l_idx + 1, max(l_idx + 1, end_l_idx)),
                level=lvl,
                quant_rule_count=q_cnt,
                table_count=t_cnt,
                has_mermaid="```mermaid" in sec_text,
            )
        )

    # Nest H3 under H2 (and H4 under H3)
    top_nodes: list[TreeNode] = []
    h2_cur: TreeNode | None = None
    h3_cur: TreeNode | None = None
    for node in flat_nodes:
        if node.level == 2:
            top_nodes.append(node)
            h2_cur = node
            h3_cur = None
        elif node.level == 3:
            if h2_cur is not None:
                h2_cur.children.append(node)
            else:
                top_nodes.append(node)
            h3_cur = node
        else:
            if h3_cur is not None:
                h3_cur.children.append(node)
            elif h2_cur is not None:
                h2_cur.children.append(node)
            else:
                top_nodes.append(node)

    return DocumentTree(
        page_id=page_id,
        rel_path=rel_path,
        body_hash=body_hash,
        total_chars=total_chars,
        total_lines=total_lines,
        max_depth=max_depth,
        nodes=top_nodes,
    )


def extract_card_patterns_and_constraints(
    fm: dict[str, Any],
    description: str,
    clean_body: str,
    page_type: str,
    tier: str,
    subdir: str,
) -> tuple[list[str], list[dict[str, str]], int]:
    """Extract Sirchmunk-style (patterns, constraints, abstraction_level) contract from a card."""
    # 1. AbstractionLevel (1=TECHNIQUE .. 5=PHILOSOPHY)
    if subdir == "00_core_aesthetics" or page_type == "core-rule":
        abs_level = 5
    elif page_type in ("concept-doc", "reference-case"):
        abs_level = 4
    elif tier == "workflows":
        abs_level = 3
    elif subdir == "01_craft_playbooks" and tier == "foundations":
        abs_level = 2
    else:
        abs_level = 1

    # 2. Patterns (3-5 quantitative core rules / mechanisms)
    patterns: list[str] = []
    fm_patterns = fm.get("patterns")
    if isinstance(fm_patterns, list):
        for p in fm_patterns:
            if isinstance(p, str) and p.strip():
                patterns.append(p.strip()[:180])

    if len(patterns) < MAX_PATTERNS_PER_CARD:
        in_code = False
        candidate_lines: list[tuple[int, str]] = []
        for ln in clean_body.splitlines():
            s = ln.strip()
            if s.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not s or s.startswith(("#", "| ---", "| :---", "> [!", "<!--")):
                continue
            if re.match(r"^(\d+\.|[-*])\s+", s):
                clean_ln = re.sub(r"^(\d+\.|[-*])\s+", "", s).strip()
                clean_ln = re.sub(r"\*\*([^\*]+)\*\*", r"\1", clean_ln)
                if len(clean_ln) < 15:
                    continue
                score = 0
                if _QUANT_RULE_RE.search(clean_ln):
                    score += 3
                if "**" in s:
                    score += 2
                if "严禁" in clean_ln or "必须" in clean_ln or "铁律" in clean_ln:
                    score += 2
                if len(clean_ln) > 165:
                    clean_ln = clean_ln[:162].rstrip() + "…"
                candidate_lines.append((score, clean_ln))

        candidate_lines.sort(key=lambda x: x[0], reverse=True)
        for _, cln in candidate_lines:
            if len(patterns) >= MAX_PATTERNS_PER_CARD:
                break
            if cln not in patterns:
                patterns.append(cln)

    # 3. Constraints (Barrier Edges & Negative Redlines)
    constraints: list[dict[str, str]] = []
    fm_constraints = fm.get("constraints")
    if isinstance(fm_constraints, list):
        for c in fm_constraints:
            if isinstance(c, dict) and c.get("description"):
                constraints.append({
                    "condition": str(c.get("condition", "domain_active")),
                    "severity": str(c.get("severity", "high")),
                    "description": str(c.get("description", ""))[:160],
                })

    # Extract negative boundary from description ("不适用于..." / "Not for...")
    if description:
        m_not = re.search(r"(不适用于[^。；\n]+|禁止用于[^。；\n]+|Not for [^.;\n]+)", description, re.IGNORECASE)
        if m_not and len(constraints) < MAX_CONSTRAINTS_PER_CARD:
            constraints.append({
                "condition": "scope_exclusion",
                "severity": "high",
                "description": m_not.group(1).strip()[:160],
            })

    # Extract explicit redline constraints from body ("严禁..." / "NEVER...")
    for ln in clean_body.splitlines():
        if len(constraints) >= MAX_CONSTRAINTS_PER_CARD:
            break
        s = ln.strip()
        if ("严禁" in s or "绝对禁止" in s or "NEVER " in s) and not s.startswith(("#", "<!--")):
            clean_c = re.sub(r"^(\d+\.|[-*>])\s*", "", s)
            clean_c = re.sub(r"\*\*([^\*]+)\*\*", r"\1", clean_c).strip()
            if 12 <= len(clean_c) <= 180 and not any(c["description"] == clean_c for c in constraints):
                constraints.append({
                    "condition": "hard_redline",
                    "severity": "high",
                    "description": clean_c[:160],
                })

    return patterns[:MAX_PATTERNS_PER_CARD], constraints[:MAX_CONSTRAINTS_PER_CARD], abs_level


@dataclass
class WikiPageInfo:
    abs_path: Path
    rel_path: str  # relative to wiki/, e.g. "foundations/visual_aesthetics/01_craft_playbooks/hue-shift.md"
    tier: str      # "foundations" | "tools_craft" | "workflows"
    domain: str    # e.g. "visual_aesthetics" | "krita_2d_paint"
    subdir: str    # e.g. "01_craft_playbooks" or ""
    page_id: str
    page_type: str
    title: str
    description: str
    epistemic_status: str
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    scripts: list[dict[str, str]] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    clean_body: str = ""
    body_hash: str = ""
    patterns: list[str] = field(default_factory=list)
    constraints: list[dict[str, str]] = field(default_factory=list)
    abstraction_level: int = 2
    doc_tree: DocumentTree | None = None


def discover_wiki_pages(repo_root: Path) -> list[WikiPageInfo]:
    """Scan all knowledge pages under wiki/{foundations,tools_craft,workflows}/."""
    wiki_root = repo_root / "wiki"
    pages: list[WikiPageInfo] = []
    for tier in TIERS:
        tier_dir = wiki_root / tier
        if not tier_dir.is_dir():
            continue
        for md_file in sorted(tier_dir.rglob("*.md")):
            if md_file.name in RESERVED_FILENAMES or "_subtrees" in md_file.parts:
                continue
            rel = md_file.relative_to(wiki_root).as_posix()
            parts = rel.split("/")
            domain = parts[1] if len(parts) > 1 else ""
            subdir = parts[2] if len(parts) > 3 else ""

            raw_text = md_file.read_text(encoding="utf-8")
            parsed = parse_markdown(raw_text)
            fm = parsed.frontmatter
            clean_body = strip_deps_banner(parsed.body)
            b_hash = sha256_text(clean_body.strip())

            raw_scripts = fm.get("scripts", [])
            norm_scripts: list[dict[str, str]] = []
            if isinstance(raw_scripts, list):
                for item in raw_scripts:
                    if isinstance(item, dict) and "path" in item:
                        norm_scripts.append({"role": str(item.get("role", "helper")), "path": str(item["path"])})
                    elif isinstance(item, dict):
                        for k, v in item.items():
                            norm_scripts.append({"role": str(k), "path": str(v)})
                    elif isinstance(item, str):
                        norm_scripts.append({"role": "script", "path": item})

            page_id = str(fm.get("id", md_file.stem))
            page_type = str(fm.get("type", ""))
            desc = str(fm.get("description", ""))
            patterns, constraints, abs_lvl = extract_card_patterns_and_constraints(
                fm, desc, clean_body, page_type, tier, subdir
            )
            doc_tree = build_document_tree(page_id, rel, clean_body, b_hash)

            pages.append(
                WikiPageInfo(
                    abs_path=md_file,
                    rel_path=rel,
                    tier=tier,
                    domain=domain,
                    subdir=subdir,
                    page_id=page_id,
                    page_type=page_type,
                    title=str(fm.get("title", md_file.stem)),
                    description=desc,
                    epistemic_status=str(fm.get("epistemic_status", "canonical")),
                    tags=[str(t) for t in fm.get("tags", [])] if isinstance(fm.get("tags"), list) else [],
                    aliases=[str(a) for a in fm.get("aliases", [])] if isinstance(fm.get("aliases"), list) else [],
                    depends_on=[str(d) for d in fm.get("depends_on", [])] if isinstance(fm.get("depends_on"), list) else [],
                    scripts=norm_scripts,
                    frontmatter=fm,
                    clean_body=clean_body,
                    body_hash=b_hash,
                    patterns=patterns,
                    constraints=constraints,
                    abstraction_level=abs_lvl,
                    doc_tree=doc_tree,
                )
            )
    return pages
