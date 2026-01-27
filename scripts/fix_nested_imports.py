#!/usr/bin/env python3
"""
Универсальный скрипт для исправления вложенных импортов.

Ищет строки с отступом и 'from ... import ...', перемещает их на top-level
и удаляет вложенные импорты.
"""
import re
import sys
from pathlib import Path

ROOT = Path('.').resolve()
PAT = re.compile(r'^(\s+)from\s+([A-Za-z0-9_.]+)\s+import\s+([A-Za-z0-9_*,\s]+)\s*$')

def process_file(p: Path):
    """Обрабатывает один файл, исправляя вложенные импорты."""
    try:
        src = p.read_text(encoding='utf-8')
    except Exception as e:
        print(f"[WARN] Cannot read {p}: {e}")
        return False, []
    
    lines = src.splitlines()
    nested = []
    for i, line in enumerate(lines):
        m = PAT.match(line)
        if m:
            indent, module, names = m.groups()
            # treat as nested import if leading whitespace present (indent has spaces/tabs)
            if indent and len(indent) > 0:
                nested.append((i, line, module.strip(), names.strip()))
    
    if not nested:
        return False, []

    # make backup
    bak = p.with_suffix(p.suffix + '.bak')
    bak.write_text(src, encoding='utf-8')
    print(f"[INFO] Backup saved: {bak}")

    # collect top-level imports
    has_top_level = set()
    for line in lines:
        tm = re.match(r'^from\s+([A-Za-z0-9_.]+)\s+import\s+([A-Za-z0-9_*,\s]+)\s*$', line)
        if tm:
            has_top_level.add((tm.group(1).strip(), tm.group(2).strip()))

    # we will build new lines
    new_lines = []
    inserted_imports = []
    removed_indices = set(i for i,_,_,_ in nested)

    # prepare list of imports to add
    imports_to_add = []
    for _, _, module, names in nested:
        key = (module, names)
        if key not in has_top_level and key not in imports_to_add:
            imports_to_add.append(key)

    # find insertion point: after shebang and encoding and initial comments and before first non-import code
    insert_at = 0
    for idx, line in enumerate(lines):
        if idx == 0 and line.startswith('#!'):
            insert_at = 1
            continue
        # skip initial empty lines or comments or docstring start
        if line.strip() == '' or line.strip().startswith('#') or (line.strip().startswith('"""') and '"""' in line and line.strip().endswith('"""')):
            insert_at = idx + 1
            continue
        # if we see "import" statements, continue sliding
        if re.match(r'^(import\s+|from\s+)', line):
            insert_at = idx + 1
            continue
        break

    # build output by skipping removed_indices and inserting imports
    for idx, line in enumerate(lines):
        if idx == insert_at and imports_to_add:
            for module, names in imports_to_add:
                new_lines.append(f'from {module} import {names}')
            # blank line after inserted imports for readability
            new_lines.append('')
        if idx in removed_indices:
            # skip the nested import line
            print(f"[FIX] Removing nested import in {p}:{idx+1} -> {lines[idx].strip()}")
            continue
        new_lines.append(line)

    p.write_text("\n".join(new_lines) + "\n", encoding='utf-8')
    return True, imports_to_add

def main():
    """Главная функция скрипта."""
    files = list(ROOT.glob('**/*.py'))
    modified = []
    for f in files:
        # skip venv and .git and __pycache__
        if any(part.startswith('.venv') or part == '.git' or part == '__pycache__' or part.startswith('.') for part in f.parts):
            continue
        ok, added = process_file(f)
        if ok:
            modified.append((f, added))
    
    if not modified:
        print("[INFO] No nested imports found.")
        return 0
    
    print("\n[INFO] Modified files:")
    for f, added in modified:
        print(f" - {f}  (added imports: {added})")
    
    # show git diff for review
    print("\n\nRun the following to review changes:\n  git status --porcelain\n  git diff\n")
    return 0

if __name__ == '__main__':
    sys.exit(main())
