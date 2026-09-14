"""Finding and reading projects. No user interface in here on purpose.

Keeping the analysis separate from the window means it can be tested without
opening one, and it means the same code can answer a spoken question as well
as draw a panel.

Standard library only.
"""
import json
import os
import re
import subprocess
import time

# A folder is a project if it contains one of these. Deliberately a marker
# file rather than "has lots of files": Downloads has lots of files.
MARKERS = {
    ".git": "git repository",
    "package.json": "Node",
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "CMakeLists.txt": "C/C++",
    "docker-compose.yml": "Docker",
    "docker-compose.yaml": "Docker",
    "Dockerfile": "Docker",
    "composer.json": "PHP",
    "Gemfile": "Ruby",
}

# Never descend into these. They are enormous, they are not your code, and
# counting them makes every number meaningless.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea",
    ".vscode", "bin", "obj", ".tox", ".mypy_cache", ".pytest_cache",
    "site-packages", ".gradle", "Pods", "coverage", ".cache",
}

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java",
    ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".sql": "SQL",
    ".sh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".md": "Markdown",
    ".toml": "TOML", ".xml": "XML", ".vue": "Vue", ".svelte": "Svelte",
    ".kt": "Kotlin", ".swift": "Swift", ".lua": "Lua", ".r": "R",
}

# Files that are the obvious place to start reading a project.
ENTRY_NAMES = [
    "main.py", "app.py", "__main__.py", "manage.py", "index.js", "index.ts",
    "main.js", "main.ts", "server.js", "app.js", "Program.cs", "main.go",
    "main.rs", "index.html", "Dockerfile", "docker-compose.yml",
]

TODO_PATTERN = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b[:\s]?(.{0,90})", re.I)

TEXT_EXTENSIONS = set(LANGUAGES) | {".txt", ".cfg", ".ini", ".env.example"}


def run_git(path, *args, timeout=6):
    """Run a git command in a project, or return None.

    Bounded: a repository on a disconnected network drive will otherwise hang
    the whole scan, and a project panel that never appears is worse than one
    with a blank git section.
    """
    try:
        result = subprocess.run(
            ["git"] + list(args), cwd=path, capture_output=True, text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def find_projects(roots, max_depth=3):
    """Look for project markers, a few levels down from each root.

    Depth-limited rather than a full walk: projects live near the top of a
    drive or inside one folder of them, and a full recursive search of a 500GB
    disk to find them is not a trade anybody wants.
    """
    found = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        _descend(root, 0, max_depth, found)
    return sorted(found.values(), key=lambda p: p["modified"], reverse=True)


def _descend(path, depth, max_depth, found):
    if depth > max_depth:
        return
    try:
        entries = list(os.scandir(path))
    except OSError:
        return

    names = {e.name for e in entries}
    markers = [m for m in MARKERS if m in names]
    if markers:
        # normpath, because a root given as "G:\\" propagates its doubled
        # separator into every path below it and the display reads G:\\Thing.
        tidy = os.path.normpath(path)
        found[os.path.normcase(tidy)] = {
            "name": os.path.basename(tidy.rstrip("\\/")) or tidy,
            "path": tidy,
            "markers": markers,
            "kinds": sorted({MARKERS[m] for m in markers}),
            "modified": _recent_mtime(entries),
        }
        # A project may contain sub-projects, but not usually, and descending
        # into one turns a monorepo into fifty entries. Stop here.
        return

    for entry in entries:
        if entry.is_dir() and entry.name not in SKIP_DIRS \
                and not entry.name.startswith("."):
            _descend(entry.path, depth + 1, max_depth, found)


def _recent_mtime(entries):
    newest = 0
    for entry in entries:
        try:
            newest = max(newest, entry.stat().st_mtime)
        except OSError:
            pass
    return newest


def analyse(path, max_files=4000):
    """Everything worth knowing about one project."""
    report = {
        "name": os.path.basename(path.rstrip("\\/")) or path,
        "path": path,
        "languages": {}, "files": 0, "lines": 0, "bytes": 0,
        "todos": [], "entries": [], "recent": [], "readme": None,
        "dependencies": {}, "git": None, "truncated": False,
    }

    week_ago = time.time() - 7 * 86400
    counted = 0

    for folder, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in files:
            if counted >= max_files:
                report["truncated"] = True
                break
            full = os.path.join(folder, filename)
            extension = os.path.splitext(filename)[1].lower()

            try:
                stat = os.stat(full)
            except OSError:
                continue
            counted += 1
            report["files"] += 1
            report["bytes"] += stat.st_size

            if stat.st_mtime > week_ago:
                report["recent"].append(
                    (os.path.relpath(full, path), stat.st_mtime))

            if filename in ENTRY_NAMES and os.path.dirname(full) == path:
                report["entries"].append(filename)

            language = LANGUAGES.get(extension)
            if language:
                report["languages"][language] = report["languages"].get(language, 0) + 1

            if extension in TEXT_EXTENSIONS and stat.st_size < 400_000:
                report["lines"] += _scan_text(full, path, report)
        if counted >= max_files:
            break

    report["recent"].sort(key=lambda r: r[1], reverse=True)
    report["recent"] = report["recent"][:12]
    report["todos"] = report["todos"][:40]
    report["readme"] = _read_readme(path)
    report["dependencies"] = _dependencies(path)
    report["git"] = _git(path)
    return report


def _scan_text(full, root, report):
    """Count lines and pick up TODO markers in one pass.

    One read rather than two: opening every source file twice doubles the
    slowest part of the scan for no benefit.
    """
    lines = 0
    try:
        with open(full, "r", encoding="utf-8", errors="ignore") as handle:
            for number, line in enumerate(handle, 1):
                lines += 1
                if len(report["todos"]) < 200 and ("TODO" in line or "FIXME" in line
                                                   or "HACK" in line or "XXX" in line):
                    match = TODO_PATTERN.search(line)
                    if match:
                        report["todos"].append({
                            "file": os.path.relpath(full, root),
                            "line": number,
                            "kind": match.group(1).upper(),
                            "text": match.group(2).strip()[:90],
                        })
    except OSError:
        return 0
    return lines


def _read_readme(path):
    for name in ("README.md", "readme.md", "README.txt", "README"):
        full = os.path.join(path, name)
        if os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as handle:
                    text = handle.read(4000)
            except OSError:
                return None
            # The first real paragraph: skip the title, badges and blank lines.
            for block in re.split(r"\n\s*\n", text):
                cleaned = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", block)
                cleaned = re.sub(r"[#*`>_-]", "", cleaned).strip()
                if len(cleaned) > 40:
                    return re.sub(r"\s+", " ", cleaned)[:400]
            return None
    return None


def _dependencies(path):
    """Direct dependencies, from whichever manifest the project uses."""
    found = {}
    package = os.path.join(path, "package.json")
    if os.path.isfile(package):
        try:
            with open(package, "r", encoding="utf-8", errors="ignore") as handle:
                data = json.load(handle)
            found["npm"] = sorted(list(data.get("dependencies", {})))
            dev = sorted(list(data.get("devDependencies", {})))
            if dev:
                found["npm (dev)"] = dev
            if data.get("scripts"):
                found["scripts"] = sorted(data["scripts"])
        except (OSError, ValueError):
            pass

    requirements = os.path.join(path, "requirements.txt")
    if os.path.isfile(requirements):
        try:
            with open(requirements, "r", encoding="utf-8", errors="ignore") as handle:
                names = []
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    names.append(re.split(r"[=<>!~\[; ]", line)[0])
            found["pip"] = sorted(n for n in names if n)
        except OSError:
            pass
    return found


def _git(path):
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    info = {}
    info["branch"] = run_git(path, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    status = run_git(path, "status", "--porcelain")
    info["dirty"] = len([l for l in (status or "").split("\n") if l.strip()])
    info["remote"] = run_git(path, "remote", "get-url", "origin")

    log = run_git(path, "log", "-8", "--pretty=%h\x1f%an\x1f%ar\x1f%s")
    commits = []
    for line in (log or "").split("\n"):
        if line.count("\x1f") == 3:
            sha, author, when, subject = line.split("\x1f")
            commits.append({"sha": sha, "author": author, "when": when,
                            "subject": subject})
    info["commits"] = commits

    # Commits per day for the last fortnight, for a sparkline.
    since = run_git(path, "log", "--since=14.days", "--pretty=%cd",
                    "--date=format:%Y-%m-%d")
    counts = {}
    for day in (since or "").split("\n"):
        if day.strip():
            counts[day.strip()] = counts.get(day.strip(), 0) + 1
    info["activity"] = counts

    total = run_git(path, "rev-list", "--count", "HEAD")
    info["total_commits"] = int(total) if (total or "").isdigit() else None
    return info


def human_bytes(value):
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return "%.0f %s" % (value, suffix) if suffix == "B" else "%.1f %s" % (value, suffix)
        value /= 1024
    return "%.1f PB" % value


def summarise(report):
    """One or two sentences, for Mavis to read aloud.

    Leads with whatever is notable rather than reciting every field - the
    same rule the PowerShell features follow, because it is read out and a
    list of six numbers is unusable as speech.
    """
    parts = []
    name = report["name"]
    top = sorted(report["languages"].items(), key=lambda kv: kv[1], reverse=True)[:2]
    language = " and ".join(l for l, _ in top) if top else "no recognised code"

    parts.append("%s is %s, about %s lines of %s across %d files"
                 % (name,
                    " and ".join(report.get("kinds", [])) or "a project",
                    "{:,}".format(report["lines"]), language, report["files"]))

    git = report.get("git")
    if git:
        if git["dirty"]:
            parts.append("on branch %s with %d uncommitted change%s"
                         % (git["branch"], git["dirty"],
                            "" if git["dirty"] == 1 else "s"))
        else:
            parts.append("on branch %s, everything committed" % git["branch"])
        if git["commits"]:
            parts.append("last commit %s, %s"
                         % (git["commits"][0]["when"], git["commits"][0]["subject"]))

    if report["todos"]:
        parts.append("%d TODO%s in the code"
                     % (len(report["todos"]), "" if len(report["todos"]) == 1 else "s"))

    return ". ".join(parts) + "."
