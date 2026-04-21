"""
Memory Manager — Controls memory file sizes for expert agents.

Prevents memory bloat by enforcing byte-level caps and compacting
Lessons Learned entries when they exceed thresholds.

Key design decisions:
- MAX_MEMORY_BYTES (3000 chars ~750 tokens) keeps memory under 1K tokens per agent
- Lessons Learned are trimmed to most recent N entries AND byte cap
- Self-Rules section is enforced to max 5 bullet points
- Performance/Recent Outcomes are auto-managed by feedback_manager (untouched here)
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Hard caps
MAX_MEMORY_BYTES = 3000        # ~750 tokens — keeps total under 1K/agent
MAX_LESSON_ENTRIES = 10        # Max lesson entries (was 20, too many)
MAX_LESSON_CHARS = 200         # Max chars PER lesson entry (force one-liners)
MAX_SELF_RULES = 5             # Max bullet points in Self-Rules


def compact_memory(mem_path: Path) -> bool:
    """Compact a memory file to stay within size limits.

    Returns True if the file was modified.
    """
    if not mem_path.exists():
        return False

    content = mem_path.read_text(encoding="utf-8")
    original = content

    # 1. Trim Lessons Learned entries
    content = _trim_lessons(content)

    # 2. Trim Self-Rules
    content = _trim_self_rules(content)

    # 3. If still over budget, aggressively trim lessons
    if len(content) > MAX_MEMORY_BYTES:
        content = _aggressive_trim_lessons(content)

    # 4. Final safety — if still over, truncate Lessons to 3
    if len(content) > MAX_MEMORY_BYTES:
        content = _hard_trim_lessons(content, max_entries=3)

    if content != original:
        mem_path.write_text(content, encoding="utf-8")
        logger.info(
            f"Compacted {mem_path.name}: {len(original)} -> {len(content)} chars "
            f"({len(original) - len(content)} saved)"
        )
        return True
    return False


def write_lesson(mem_path: Path, replay_date: str, note_text: str) -> bool:
    """Write a single lesson note, enforcing all size limits.

    Prepends newest first, trims to MAX_LESSON_ENTRIES, enforces
    per-entry char limit, and compacts if total exceeds MAX_MEMORY_BYTES.

    Returns True if file was updated.
    """
    if not mem_path.exists():
        return False

    content = mem_path.read_text(encoding="utf-8")

    marker = "## Lessons Learned"
    if marker not in content:
        return False

    # Truncate note to max chars
    note_text = note_text.strip()
    if len(note_text) > MAX_LESSON_CHARS:
        note_text = note_text[:MAX_LESSON_CHARS - 3] + "..."

    note_line = f"- [{replay_date}] {note_text}\n"

    # Insert after marker header line
    idx = content.index(marker)
    end_of_line = content.index("\n", idx) + 1

    # Skip placeholder line if present
    next_line_end = content.find("\n", end_of_line)
    if next_line_end != -1:
        next_line = content[end_of_line:next_line_end].strip()
        if next_line.startswith("(starting fresh") or next_line == "":
            content = content[:end_of_line] + note_line + content[next_line_end + 1:]
        else:
            content = content[:end_of_line] + note_line + content[end_of_line:]
    else:
        content = content[:end_of_line] + note_line

    # Trim to MAX_LESSON_ENTRIES
    content = _trim_lessons(content)

    # Compact if over budget
    if len(content) > MAX_MEMORY_BYTES:
        content = _aggressive_trim_lessons(content)

    mem_path.write_text(content, encoding="utf-8")
    return True


def _trim_lessons(content: str) -> str:
    """Trim Lessons Learned to MAX_LESSON_ENTRIES entries,
    each truncated to MAX_LESSON_CHARS."""
    marker = "## Lessons Learned"
    if marker not in content:
        return content

    idx = content.index(marker)
    end_of_header = content.index("\n", idx) + 1

    # Split rest into lesson lines and non-lesson lines
    rest = content[end_of_header:]
    lines = rest.split("\n")
    lesson_lines = []
    other_lines = []

    for line in lines:
        if line.startswith("- ["):
            # Truncate long entries
            if len(line) > MAX_LESSON_CHARS + 15:  # +15 for "- [YYYY-MM-DD] " prefix
                line = line[:MAX_LESSON_CHARS + 12] + "..."
            lesson_lines.append(line)
        else:
            other_lines.append(line)

    # Keep only most recent entries
    if len(lesson_lines) > MAX_LESSON_ENTRIES:
        lesson_lines = lesson_lines[:MAX_LESSON_ENTRIES]

    return content[:end_of_header] + "\n".join(lesson_lines + other_lines)


def _trim_self_rules(content: str) -> str:
    """Trim Self-Rules to MAX_SELF_RULES bullet points."""
    marker = "## Self-Rules"
    if marker not in content:
        return content

    # Find the section boundaries
    idx = content.index(marker)
    end_of_header = content.index("\n", idx) + 1

    # Find next section (## header)
    next_section = re.search(r"\n## ", content[end_of_header:])
    if next_section:
        section_end = end_of_header + next_section.start()
    else:
        section_end = len(content)

    section_body = content[end_of_header:section_end]
    rule_lines = [l for l in section_body.split("\n") if l.strip().startswith(("-", "*"))]
    other_lines = [l for l in section_body.split("\n") if not l.strip().startswith(("-", "*"))]

    if len(rule_lines) > MAX_SELF_RULES:
        rule_lines = rule_lines[:MAX_SELF_RULES]

    new_body = "\n".join(rule_lines + other_lines)
    return content[:end_of_header] + new_body + content[section_end:]


def _aggressive_trim_lessons(content: str) -> str:
    """Reduce lessons to 5 entries with shorter truncation."""
    marker = "## Lessons Learned"
    if marker not in content:
        return content

    idx = content.index(marker)
    end_of_header = content.index("\n", idx) + 1

    rest = content[end_of_header:]
    lines = rest.split("\n")
    lesson_lines = []
    other_lines = []

    for line in lines:
        if line.startswith("- ["):
            # Aggressive truncation: 120 chars max
            if len(line) > 135:
                line = line[:132] + "..."
            lesson_lines.append(line)
        else:
            other_lines.append(line)

    lesson_lines = lesson_lines[:5]
    return content[:end_of_header] + "\n".join(lesson_lines + other_lines)


def _hard_trim_lessons(content: str, max_entries: int = 3) -> str:
    """Emergency trim — keep only N entries, 100 chars each."""
    marker = "## Lessons Learned"
    if marker not in content:
        return content

    idx = content.index(marker)
    end_of_header = content.index("\n", idx) + 1

    rest = content[end_of_header:]
    lines = rest.split("\n")
    lesson_lines = []
    other_lines = []

    for line in lines:
        if line.startswith("- ["):
            if len(line) > 115:
                line = line[:112] + "..."
            lesson_lines.append(line)
        else:
            other_lines.append(line)

    lesson_lines = lesson_lines[:max_entries]
    return content[:end_of_header] + "\n".join(lesson_lines + other_lines)


def write_self_rule(mem_path: Path, rule_text: str) -> bool:
    """Write a self-rule to the Self-Rules section, replacing the placeholder.

    Rules are durable cross-month patterns the agent wants to preserve.
    Deduplicates by checking if the rule (or a close prefix) already exists.
    Enforces MAX_SELF_RULES limit — oldest rule dropped if full.

    Returns True if file was updated.
    """
    if not mem_path.exists():
        return False

    content = mem_path.read_text(encoding="utf-8")

    marker = "## Self-Rules"
    if marker not in content:
        return False

    rule_text = rule_text.strip()
    if not rule_text:
        return False

    # Cap rule length
    if len(rule_text) > MAX_LESSON_CHARS:
        rule_text = rule_text[:MAX_LESSON_CHARS - 3] + "..."

    # Find section boundaries
    idx = content.index(marker)
    end_of_header = content.index("\n", idx) + 1

    # Skip the sub-header line "(YOUR section ...)" if present
    next_line_end = content.find("\n", end_of_header)
    if next_line_end != -1:
        next_line = content[end_of_header:next_line_end].strip()
        if next_line.startswith("(") or next_line == "":
            end_of_header = next_line_end + 1

    # Find next ## section
    next_section = re.search(r"\n## ", content[end_of_header:])
    if next_section:
        section_end = end_of_header + next_section.start()
    else:
        section_end = len(content)

    section_body = content[end_of_header:section_end]
    existing_rules = [l for l in section_body.split("\n") if l.strip().startswith(("-", "*"))]

    # Deduplicate: skip if first 60 chars match any existing rule
    rule_prefix = rule_text[:60].lower()
    for existing in existing_rules:
        existing_stripped = existing.lstrip("-* ").strip()[:60].lower()
        if rule_prefix == existing_stripped:
            return False

    # Build new rule line
    rule_line = f"- {rule_text}"

    # Add to list, enforce max (drop oldest = last in list)
    existing_rules.insert(0, rule_line)
    if len(existing_rules) > MAX_SELF_RULES:
        existing_rules = existing_rules[:MAX_SELF_RULES]

    new_body = "\n".join(existing_rules) + "\n"
    content = content[:end_of_header] + new_body + content[section_end:]

    mem_path.write_text(content, encoding="utf-8")
    logger.info(f"Wrote self-rule to {mem_path.name}: {rule_text[:80]}")
    return True


def compact_all_memories(memory_dir: Path) -> int:
    """Compact all memory files in a directory. Returns count modified."""
    count = 0
    for md_file in memory_dir.glob("*.md"):
        if compact_memory(md_file):
            count += 1
    return count
