#!/usr/bin/env python3
"""Check local FiveCircles configuration and document references, without network calls."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


def check(root: Path) -> list[str]:
    errors: list[str] = []
    root = root.resolve()

    def local_path(value: object, label: str) -> Path | None:
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            errors.append(f'{label}: expected non-empty relative path')
            return None
        target = (root / value).resolve()
        if not target.is_relative_to(root):
            errors.append(f'{label}: path leaves project root: {value}')
            return None
        if not target.exists():
            errors.append(f'{label}: missing path: {value}')
        return target

    def read_json(relative: str) -> dict:
        try:
            obj = json.loads((root / relative).read_text(encoding='utf-8'))
            if not isinstance(obj, dict):
                raise ValueError('expected JSON object')
            return obj
        except (OSError, ValueError) as exc:
            errors.append(f'{relative}: {exc}')
            return {}

    config = read_json('fivecircles/project.json')
    manifest = read_json('fivecircles/upstream-manifest.json')
    for key in ('project', 'timezone', 'repository', 'framework', 'roles', 'paths', 'commands', 'milestones'):
        if not config.get(key):
            errors.append(f'project.json: missing {key}')
    paths = config.get('paths', {})
    if isinstance(paths, dict):
        for key, value in paths.items():
            local_path(value, f'paths.{key}')
    else:
        errors.append('project.json: paths must be an object')
    commands = config.get('commands', {})
    if isinstance(commands, dict):
        for name, command in commands.items():
            if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
                errors.append(f'commands.{name}: expected non-empty argv list')
    else:
        errors.append('project.json: commands must be an object')
    milestones = config.get('milestones', [])
    dates = []
    ids = set()
    if not isinstance(milestones, list):
        errors.append('project.json: milestones must be an array')
        milestones = []
    for item in milestones:
        try:
            if not isinstance(item, dict) or not item.get('id') or not item.get('scope'):
                raise ValueError('missing id or scope')
            if item['id'] in ids:
                raise ValueError('duplicate milestone id')
            ids.add(item['id'])
            dates.append(date.fromisoformat(item['date']))
        except (TypeError, KeyError, ValueError) as exc:
            errors.append(f'milestone: {exc}')
    if dates != sorted(dates):
        errors.append('project.json: milestone dates are out of order')
    framework = config.get('framework', {})
    if not isinstance(framework, dict):
        errors.append('project.json: framework must be an object')
        framework = {}
    commit = manifest.get('source_commit', '')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        errors.append('manifest: invalid source commit')
    if framework.get('commit') != commit or framework.get('url') != manifest.get('source_url'):
        errors.append('manifest: framework source differs from project.json')
    entries = manifest.get('files', [])
    if not isinstance(entries, list) or not entries:
        errors.append('manifest: missing imported files')
        entries = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append('manifest: file entry must be an object')
            continue
        relative = entry.get('installed_path')
        target = local_path(relative, 'manifest file')
        if isinstance(relative, str):
            if relative in seen:
                errors.append(f'manifest: duplicate path {relative}')
            seen.add(relative)
        for key in ('upstream_sha256', 'installed_sha256'):
            value = entry.get(key, '')
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
                errors.append(f'manifest: invalid {key} for {relative}')
        if target and target.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != entry.get('installed_sha256'):
                errors.append(f'manifest: local modification needs review: {relative}')
        if entry.get('modified') != (entry.get('upstream_sha256') != entry.get('installed_sha256')):
            errors.append(f'manifest: inconsistent modified flag: {relative}')
    license_file = local_path('fivecircles/LICENSE', 'license')
    if license_file and license_file.is_file() and 'Creative Commons Attribution 4.0' not in license_file.read_text():
        errors.append('license: original attribution missing')
    skills = [root/'fivecircles/agent/skills/fivecircles/SKILL.md', root/'.agents/skills/fivecircles/SKILL.md']
    if not all(x.is_file() for x in skills):
        errors.append('skill: both local entry points must exist')
    elif skills[0].read_bytes() != skills[1].read_bytes():
        errors.append('skill: local entry points differ')
    documents = sorted((root/'fivecircles').rglob('*.md'))
    documents += [root/'AGENTS.md', skills[1]]
    for doc in documents:
        if not doc.is_file():
            errors.append(f'missing document: {doc.relative_to(root)}')
            continue
        body = doc.read_text(encoding='utf-8')
        if re.search(r'nospoiler|bit-ts|Team C|architecture/specs/|fivecircles/specs/', body, re.I):
            errors.append(f'legacy project/path reference: {doc.relative_to(root)}')
        body = re.sub(r'```.*?```', '', body, flags=re.S)
        for destination in re.findall(r'\[[^\]]*\]\(([^)]+)\)', body):
            destination = destination.strip('<>')
            parsed = urlsplit(destination)
            if parsed.scheme or destination.startswith('#'):
                continue
            path = unquote(parsed.path)
            if path:
                target = (doc.parent/path).resolve()
                if not target.is_relative_to(root) or not target.exists():
                    errors.append(f'broken link: {doc.relative_to(root)} -> {destination}')
    for git_path in (root/'fivecircles').rglob('.git'):
        errors.append(f'nested Git directory: {git_path.relative_to(root)}')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = check(args.root)
    if errors:
        for error in errors:
            print('FAIL:', error, file=sys.stderr)
        return 1
    print('PASS: FiveCircles configuration, source attribution, local links and skill entries')
    print('Scope: local workflow configuration only; no API, GPU, Git push or deployment performed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
