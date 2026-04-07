#!/usr/bin/env python3
"""
Repository Geology Visualizer

Visualizes the geological history of a git repository by showing
how code from different release branches survives over time.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from tqdm import tqdm


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Visualize the geological history of a git repository"
    )
    parser.add_argument(
        "repo_path",
        type=str,
        help="Path to the git repository to analyze"
    )
    parser.add_argument(
        "--x-axis",
        choices=["commit", "date"],
        default="date",
        help="What to use for the x-axis: commit number or date (default: date)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path for the chart (default: display interactively)"
    )
    parser.add_argument(
        "--release-pattern",
        type=str,
        default=r"^ccf-\d+\.\d+\.\d+$",
        help="Regex pattern to match release tags (default: ^ccf-\\d+\\.\\d+\\.\\d+$)"
    )
    parser.add_argument(
        "--main-branch",
        type=str,
        default=None,
        help="Name of the main branch (default: auto-detect main/master)"
    )
    parser.add_argument(
        "--extensions",
        type=str,
        nargs="+",
        default=None,
        help="File extensions to analyze (e.g., .cpp .h .py). Default: all files"
    )
    parser.add_argument(
        "--facet-languages",
        action="store_true",
        help="Create separate faceted charts for language groups: JavaScript/TypeScript, C++, Python"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Directory to store cache files (default: .geology_cache in repo)"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force recomputation, ignoring any cached data"
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Only use cached data, fail if not available"
    )
    parser.add_argument(
        "--initial-commit",
        type=str,
        default="7e33052",
        help="Commit hash (or prefix) for the pre-release anchor point. Code before the first tagged release is attributed to 'pre-release'. (default: 7e33052)"
    )
    return parser.parse_args()


def get_cache_key(repo_path: Path, extensions: Optional[List[str]], release_pattern: str) -> str:
    """Generate a cache key based on analysis parameters."""
    ext_str = ",".join(sorted(extensions)) if extensions else "all"
    key_str = f"{repo_path}:{ext_str}:{release_pattern}"
    return hashlib.md5(key_str.encode()).hexdigest()[:12]


def get_cache_path(repo_path: Path, cache_dir: Optional[str], extensions: Optional[List[str]], release_pattern: str) -> Path:
    """Get the path to the cache file."""
    if cache_dir:
        cache_base = Path(cache_dir)
    else:
        cache_base = repo_path / ".geology_cache"
    
    cache_base.mkdir(parents=True, exist_ok=True)
    cache_key = get_cache_key(repo_path, extensions, release_pattern)
    return cache_base / f"geology_{cache_key}.json"


def save_cache(cache_path: Path, dates: List[datetime], layer_data: Dict[str, List[int]], 
               releases: List['ReleaseInfo'], main_branch: str):
    """Save computed geology data to cache."""
    cache_data = {
        "version": 5,
        "computed_at": datetime.now().isoformat(),
        "main_branch": main_branch,
        "releases": [
            {"name": r.name, "tag_ref": r.tag_ref, "major": r.major,
             "commit": r.commit, "commit_date": r.commit_date.isoformat()}
            for r in releases
        ],
        "dates": [d.isoformat() for d in dates],
        "layer_data": layer_data,
    }
    with open(cache_path, "w") as f:
        json.dump(cache_data, f, indent=2)
    print(f"  Cache saved to: {cache_path}")


def load_cache(cache_path: Path, releases: List['ReleaseInfo'] = None) -> Optional[Tuple[List[datetime], Dict[str, List[int]], List['ReleaseInfo'], str]]:
    """Load cached geology data if available and valid.
    
    If releases is provided, validates that the cached releases match.
    """
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path, "r") as f:
            cache_data = json.load(f)
        
        if cache_data.get("version") != 5:
            print(f"  Cache version mismatch, will recompute")
            return None
        
        # Validate releases match
        if releases is not None:
            cached_releases = cache_data.get("releases", [])
            current_release_keys = [(r.name, r.commit, r.major) for r in releases]
            cached_release_keys = [(r["name"], r["commit"], r["major"]) for r in cached_releases]
            if current_release_keys != cached_release_keys:
                print(f"  Cache release mismatch, will recompute")
                return None
        
        dates = [datetime.fromisoformat(d) for d in cache_data["dates"]]
        layer_data = cache_data["layer_data"]
        main_branch = cache_data["main_branch"]
        releases = [
            ReleaseInfo(
                name=r["name"], tag_ref=r["tag_ref"], major=r["major"],
                commit=r["commit"], commit_date=datetime.fromisoformat(r["commit_date"])
            )
            for r in cache_data["releases"]
        ]
        
        computed_at = cache_data.get("computed_at", "unknown")
        print(f"  Loaded cache from: {cache_path} (computed: {computed_at})")
        return dates, layer_data, releases, main_branch
    
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"  Cache corrupted ({e}), will recompute")
        return None


# Language groups for faceting
LANGUAGE_GROUPS = {
    "JavaScript/TypeScript": [".js", ".ts", ".jsx", ".tsx"],
    "C++": [".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"],
    "Python": [".py"],
}


def validate_repo_path(repo_path: str) -> Path:
    """Validate that the given path is a git repository."""
    path = Path(repo_path).resolve()
    
    if not path.exists():
        print(f"Error: Path '{repo_path}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    git_dir = path / ".git"
    if not git_dir.exists():
        print(f"Error: Path '{repo_path}' is not a git repository", file=sys.stderr)
        sys.exit(1)
    
    return path


@dataclass
class ReleaseInfo:
    """Information about a release tag."""
    name: str           # e.g., "ccf-6.0.0"
    tag_ref: str        # Full ref name
    commit: str         # The commit the tag points to
    commit_date: datetime  # Date of the tagged commit
    major: int          # Major version number for grouping
    
    def __repr__(self):
        return f"ReleaseInfo({self.name}, commit={self.commit[:8]}, date={self.commit_date.date()})"


def run_git(repo_path: Path, *args: str) -> str:
    """Run a git command and return its output."""
    cmd = ["git", "-C", str(repo_path)] + list(args)
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout.strip()


def get_commit_date(repo_path: Path, commit: str) -> datetime:
    """Get the author date of a commit."""
    date_str = run_git(repo_path, "log", "-1", "--format=%aI", commit)
    # Parse ISO 8601 format
    return datetime.fromisoformat(date_str)


def find_main_branch(repo_path: Path) -> str:
    """Find the main branch name (main, master, etc.)."""
    # Check common main branch names
    for branch in ["main", "master"]:
        try:
            run_git(repo_path, "rev-parse", "--verify", f"refs/heads/{branch}")
            return branch
        except RuntimeError:
            pass
    
    # Fallback: try to get the default branch from origin
    try:
        ref = run_git(repo_path, "symbolic-ref", "refs/remotes/origin/HEAD")
        return ref.split("/")[-1]
    except RuntimeError:
        pass
    
    raise RuntimeError("Could not find main branch (tried: main, master)")


def find_release_tags(repo_path: Path, pattern: str = r"^ccf-\d+\.\d+\.\d+$") -> List[str]:
    """Find all release tags matching the pattern."""
    try:
        output = run_git(repo_path, "tag", "-l")
    except RuntimeError:
        return []
    
    tags = []
    regex = re.compile(pattern)
    
    for line in output.splitlines():
        tag = line.strip()
        if regex.search(tag):
            tags.append(tag)
    
    return sorted(tags)


def parse_version(tag: str, pattern: str) -> Optional[int]:
    """
    Extract major version from a tag name.
    
    Tries to find the first occurrence of X.Y.Z pattern in the tag.
    Returns the major version number or None if not found.
    """
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
    if match:
        return int(match.group(1))
    return None


def get_release_info_from_tags(repo_path: Path, tags: List[str], initial_commit: str = None) -> List[ReleaseInfo]:
    """
    Get release information from tags.
    
    Returns releases sorted by commit date (oldest first).
    If initial_commit is provided (already resolved), includes a synthetic
    "pre-release" entry anchored to that commit.
    """
    releases = []
    
    if initial_commit:
        try:
            commit_date = get_commit_date(repo_path, initial_commit)
            releases.append(ReleaseInfo(
                name="pre-release",
                tag_ref="",
                commit=initial_commit,
                commit_date=commit_date,
                major=-1
            ))
        except (RuntimeError, ValueError) as e:
            print(f"Warning: Could not get date for pre-release commit {initial_commit}: {e}", file=sys.stderr)
    
    for tag in tags:
        try:
            # Get the commit the tag points to
            commit = run_git(repo_path, "rev-list", "-n", "1", tag)
            commit_date = get_commit_date(repo_path, commit)
            major = parse_version(tag, "")
            
            if major is None:
                print(f"Warning: Could not parse version from tag {tag}", file=sys.stderr)
                continue
            
            releases.append(ReleaseInfo(
                name=tag,
                tag_ref=tag,
                commit=commit,
                commit_date=commit_date,
                major=major
            ))
        except RuntimeError as e:
            print(f"Warning: Could not get info for tag {tag}: {e}", file=sys.stderr)
    
    # Sort by commit date (oldest first)
    releases.sort(key=lambda r: r.commit_date)
    
    return releases


def get_first_release_per_major(releases: List[ReleaseInfo]) -> List[ReleaseInfo]:
    """
    Get the first (oldest) release for each major version.
    
    This represents when each release line was "cut" - i.e., when the first
    X.0.0 release was made.
    """
    seen = {}
    for rel in releases:
        if rel.major not in seen:
            seen[rel.major] = rel
    
    # Return sorted by commit date
    return sorted(seen.values(), key=lambda r: r.commit_date)


def analyze_repository(repo_path: Path, release_pattern: str = r"^ccf-\d+\.\d+\.\d+$", main_branch_override: str = None, initial_commit: str = None) -> Tuple[str, List[ReleaseInfo]]:
    """
    Analyze the repository to find release cut points based on tags.
    
    Returns:
        main_branch: Name of the main branch
        releases: List of ReleaseInfo sorted by commit date (first release per major.minor)
    """
    print("Analyzing repository structure...")
    
    # Find main branch
    if main_branch_override:
        main_branch = main_branch_override
    else:
        main_branch = find_main_branch(repo_path)
    print(f"  Main branch: {main_branch}")
    
    # Resolve initial commit for pre-release era
    if initial_commit:
        try:
            initial_commit = run_git(repo_path, "rev-parse", initial_commit)
        except RuntimeError as e:
            print(f"  Warning: Could not resolve initial commit '{initial_commit}': {e}", file=sys.stderr)
            initial_commit = None
    if not initial_commit:
        try:
            initial_commit = run_git(repo_path, "rev-list", "--first-parent", "--reverse", main_branch).split("\n")[0].strip()
        except RuntimeError as e:
            print(f"  Warning: Could not determine first commit on {main_branch}: {e}", file=sys.stderr)
    
    # Find release tags
    release_tags = find_release_tags(repo_path, release_pattern)
    print(f"  Found {len(release_tags)} release tags matching pattern")
    
    if not release_tags:
        print(f"  Warning: No release tags found matching '{release_pattern}' pattern")
        return main_branch, []
    
    # Get info for all tags
    all_releases = get_release_info_from_tags(repo_path, release_tags, initial_commit)
    
    # Get first release per major version (the "cut point")
    releases = get_first_release_per_major(all_releases)
    
    print(f"  Release cut points (first release per major version):")
    for rel in releases:
        label = "pre-release" if rel.major == -1 else f"{rel.major}.x"
        print(f"    {label}: {rel.name} at {rel.commit[:8]} on {rel.commit_date.date()}")
    
    return main_branch, releases


# Directories to exclude from analysis (vendored/third-party code)
EXCLUDED_DIRS = [
    "vendor/",
    "vendored/",
    "third_party/",
    "3rdparty/",
    "3rd_party/",
    "external/",
    "node_modules/",
    "deps/",
    "lib/",  # Often contains vendored code
]


def is_vendored_path(filepath: str) -> bool:
    """Check if a file path is in a vendored/third-party directory."""
    filepath_lower = filepath.lower()
    for excluded in EXCLUDED_DIRS:
        if excluded in filepath_lower or filepath_lower.startswith(excluded.rstrip("/")):
            return True
    return False


def file_matches_extensions(filepath: str, extensions: Optional[List[str]]) -> bool:
    """Check if a file matches the given extensions filter."""
    if not extensions:
        return True
    return any(filepath.endswith(ext) for ext in extensions)


# Comment prefixes by file extension
COMMENT_PREFIXES = {
    ".py": ("#",),
    ".sh": ("#",),
    ".bash": ("#",),
    ".rb": ("#",),
    ".pl": ("#",),
    ".yaml": ("#",),
    ".yml": ("#",),
    ".toml": ("#",),
    ".ini": ("#", ";"),
    ".js": ("//",),
    ".ts": ("//",),
    ".jsx": ("//",),
    ".tsx": ("//",),
    ".cpp": ("//",),
    ".cc": ("//",),
    ".cxx": ("//",),
    ".c": ("//",),
    ".h": ("//",),
    ".hpp": ("//",),
    ".hxx": ("//",),
    ".java": ("//",),
    ".cs": ("//",),
    ".go": ("//",),
    ".rs": ("//",),
    ".swift": ("//",),
}


def is_ignorable_line(line: str, filepath: str) -> bool:
    """
    Check if a line should be ignored for counting purposes.

    Returns True for whitespace-only lines and single-line comment lines.
    """
    stripped = line.strip()
    if not stripped:
        return True

    ext = os.path.splitext(filepath)[1].lower()
    prefixes = COMMENT_PREFIXES.get(ext, ())
    for prefix in prefixes:
        if stripped.startswith(prefix):
            return True

    return False


def run_blame(repo_path: Path, commit: str, filepath: str) -> Dict[str, int]:
    """
    Run git blame on a file at a specific commit.
    
    Returns a dict mapping commit hashes to line counts.
    Whitespace-only and comment lines are excluded from the count.
    """
    try:
        # Use porcelain format for easy parsing
        output = run_git(
            repo_path, "blame", "--porcelain", "-w", commit, "--", filepath
        )
    except RuntimeError:
        return {}
    
    commit_lines = defaultdict(int)
    current_commit = None

    for line in output.splitlines():
        # Porcelain format: first field of each blame entry is the commit hash
        if line and len(line) >= 40 and line[0] != '\t':
            parts = line.split()
            if parts and len(parts[0]) == 40:
                current_commit = parts[0]
        elif line.startswith('\t') and current_commit is not None:
            # This is the actual content line (prefixed with tab)
            content = line[1:]  # Strip the leading tab
            if not is_ignorable_line(content, filepath):
                commit_lines[current_commit] += 1

    return dict(commit_lines)


def attribute_commit_to_release(repo_path: Path, blamed_commit: str, releases: List[ReleaseInfo], commit_cache: Dict[str, datetime]) -> str:
    """
    Attribute a commit to a release era.
    
    Code written before release N (but after release N-1) is attributed to
    "N.x" because it shipped in release N.
    Code after the last release belongs to "unreleased".
    """
    # Get the date of the blamed commit (with caching)
    if blamed_commit not in commit_cache:
        try:
            commit_cache[blamed_commit] = get_commit_date(repo_path, blamed_commit)
        except (RuntimeError, ValueError):
            return "unknown"
    
    commit_date = commit_cache[blamed_commit]
    
    # Find which release this code shipped in.
    # Code written before release N was cut ships in release N.
    for rel in releases:
        if commit_date <= rel.commit_date:
            if rel.major == -1:
                return "pre-release"
            return f"{rel.major}.x"
    
    # After the last release - unreleased code
    return "unreleased"


def blame_file_to_eras(
    repo_path: Path,
    commit: str,
    filepath: str,
    releases: List[ReleaseInfo],
    commit_cache: Dict[str, datetime]
) -> Dict[str, int]:
    """
    Blame a single file and return era-attributed line counts.
    
    Returns a dict mapping release eras to line counts for this file.
    """
    era_lines = defaultdict(int)
    blame_result = run_blame(repo_path, commit, filepath)
    for blamed_commit, line_count in blame_result.items():
        era = attribute_commit_to_release(repo_path, blamed_commit, releases, commit_cache)
        era_lines[era] += line_count
    return dict(era_lines)


def get_changed_files_between(repo_path: Path, parent: str, commit: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Get lists of added, deleted, and modified files between two commits.
    
    Returns:
        added: Files that exist in commit but not parent
        deleted: Files that exist in parent but not commit
        modified: Files that exist in both but were changed
    """
    try:
        output = run_git(
            repo_path, "diff", "--name-status", "--no-renames", parent, commit
        )
    except RuntimeError:
        return [], [], []
    
    added = []
    deleted = []
    modified = []
    
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split('\t', 1)
        if len(parts) != 2:
            continue
        status, filepath = parts[0].strip(), parts[1].strip()
        
        if status == 'A':
            added.append(filepath)
        elif status == 'D':
            deleted.append(filepath)
        elif status.startswith('M') or status.startswith('T'):
            modified.append(filepath)
        # For copies (C) and other statuses, treat as added
        elif status.startswith('C'):
            added.append(filepath)
    
    return added, deleted, modified


def get_all_commits_on_branch(repo_path: Path, main_branch: str) -> List[Tuple[str, datetime]]:
    """
    Get all commits on the main branch in chronological order (oldest first).
    
    Returns list of (commit_hash, commit_date) tuples.
    """
    # --first-parent follows only the main branch line through merges
    output = run_git(
        repo_path, "rev-list", "--first-parent", "--reverse",
        "--format=%aI", main_branch
    )
    
    commits = []
    lines = output.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("commit "):
            commit_hash = line[7:]
            if i + 1 < len(lines):
                date_str = lines[i + 1].strip()
                try:
                    commit_date = datetime.fromisoformat(date_str)
                    commits.append((commit_hash, commit_date))
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        else:
            i += 1
    
    return commits


def compute_geology(
    repo_path: Path,
    main_branch: str,
    releases: List[ReleaseInfo],
    extensions: List[str] = None
) -> Tuple[List[datetime], Dict[str, List[int]]]:
    """
    Compute the geological history of the repository incrementally.
    
    Instead of running git blame on every file at each sample point,
    this walks every commit on the main branch and incrementally updates
    a running attribution snapshot by only re-blaming files that changed.
    
    Returns:
        dates: List of commit dates (one per commit on main)
        layer_data: Dict mapping release eras to lists of line counts
    """
    print("\nComputing code geology (incremental)...")
    
    # Get all commits on main branch
    all_commits = get_all_commits_on_branch(repo_path, main_branch)
    print(f"  Found {len(all_commits)} commits on {main_branch}")
    
    if not all_commits:
        return [], {}
    
    # Initialize eras
    eras = ["pre-release"] + [f"{rel.major}.x" for rel in releases] + ["unreleased"]
    layer_data: Dict[str, List[int]] = {era: [] for era in eras}
    dates: List[datetime] = []
    
    # Running totals: global era → line count
    totals: Dict[str, int] = {era: 0 for era in eras}
    
    # Per-file attribution: filepath → {era: line_count}
    file_eras: Dict[str, Dict[str, int]] = {}
    
    # Cache for commit dates to avoid repeated git calls
    commit_cache: Dict[str, datetime] = {}
    for rel in releases:
        commit_cache[rel.commit] = rel.commit_date
    # Pre-populate with all main-branch commit dates
    for commit_hash, commit_date in all_commits:
        commit_cache[commit_hash] = commit_date
    
    # Process first commit: full blame of all files
    first_hash, first_date = all_commits[0]
    
    try:
        tree_output = run_git(repo_path, "ls-tree", "-r", "--name-only", first_hash)
        all_files = tree_output.splitlines()
    except RuntimeError:
        all_files = []
    
    bootstrap_files = [
        f for f in all_files
        if not is_vendored_path(f) and file_matches_extensions(f, extensions)
    ]
    
    for filepath in tqdm(bootstrap_files, desc="  Bootstrapping", unit="file", smoothing=0.1):
        file_era_counts = blame_file_to_eras(
            repo_path, first_hash, filepath, releases, commit_cache
        )
        file_eras[filepath] = file_era_counts
        for era, count in file_era_counts.items():
            if era not in totals:
                totals[era] = 0
                layer_data[era] = []
            totals[era] += count
    
    # Record first data point
    dates.append(first_date)
    for era in layer_data:
        layer_data[era].append(totals.get(era, 0))
    
    total_lines = sum(totals.values())
    print(f"  Bootstrap complete: {total_lines} lines across {len(file_eras)} files")
    
    # Process remaining commits incrementally
    for idx in tqdm(range(1, len(all_commits)), desc="  Commits", unit="commit", smoothing=0.1):
        commit_hash, commit_date = all_commits[idx]
        prev_hash = all_commits[idx - 1][0]
        
        # Get files changed between parent and this commit
        added, deleted, modified = get_changed_files_between(repo_path, prev_hash, commit_hash)
        
        # Filter to relevant files
        added = [f for f in added if not is_vendored_path(f) and file_matches_extensions(f, extensions)]
        deleted = [f for f in deleted if not is_vendored_path(f) and file_matches_extensions(f, extensions)]
        modified = [f for f in modified if not is_vendored_path(f) and file_matches_extensions(f, extensions)]
        
        # Handle deleted files: subtract their old attribution
        for filepath in deleted:
            old_eras = file_eras.pop(filepath, {})
            for era, count in old_eras.items():
                totals[era] = totals.get(era, 0) - count
        
        # Handle added files: blame them at the new commit
        for filepath in added:
            new_eras = blame_file_to_eras(
                repo_path, commit_hash, filepath, releases, commit_cache
            )
            file_eras[filepath] = new_eras
            for era, count in new_eras.items():
                if era not in totals:
                    totals[era] = 0
                    layer_data[era] = [0] * len(dates)
                totals[era] += count
        
        # Handle modified files: blame before and after, apply delta
        for filepath in modified:
            # Remove old attribution
            old_eras = file_eras.get(filepath, {})
            for era, count in old_eras.items():
                totals[era] = totals.get(era, 0) - count
            
            # Add new attribution
            new_eras = blame_file_to_eras(
                repo_path, commit_hash, filepath, releases, commit_cache
            )
            file_eras[filepath] = new_eras
            for era, count in new_eras.items():
                if era not in totals:
                    totals[era] = 0
                    layer_data[era] = [0] * len(dates)
                totals[era] += count
        
        # Record data point
        dates.append(commit_date)
        for era in layer_data:
            layer_data[era].append(totals.get(era, 0))
    
    return dates, layer_data


def plot_geology_faceted(
    x_values,
    facet_data: Dict[str, Dict[str, List[int]]],
    x_axis_type: str,
    output_path: str = None
):
    """
    Create a faceted stacked area chart showing code geology for each language group.
    
    Args:
        x_values: Array of x-axis values (dates or indices)
        facet_data: Dict mapping language group names to their layer_data dicts
        x_axis_type: Either "commit" or "date"
        output_path: If provided, save to this file instead of displaying
    """
    # Filter to only language groups with data
    active_facets = {k: v for k, v in facet_data.items() 
                     if any(sum(lines) > 0 for lines in v.values())}
    
    n_facets = len(active_facets)
    if n_facets == 0:
        print("No data found for any language group")
        return
    
    fig, axes = plt.subplots(n_facets, 1, figsize=(14, 5 * n_facets), sharex=True)
    
    # Handle single facet case
    if n_facets == 1:
        axes = [axes]
    
    # Get a consistent color map across all facets
    all_eras = set()
    for layer_data in active_facets.values():
        all_eras.update(k for k, v in layer_data.items() if sum(v) > 0)
    all_eras = sorted(all_eras, key=lambda x: (x != "pre-release", x))
    cmap = plt.cm.tab20
    era_colors = {era: cmap(i % 20 / 20)
                  for i, era in enumerate(all_eras)}
    
    for ax, (lang_name, layer_data) in zip(axes, active_facets.items()):
        # Filter out empty eras but maintain consistent order
        labels = [k for k in all_eras if k in layer_data and sum(layer_data[k]) > 0]
        if not labels:
            ax.text(0.5, 0.5, f"No {lang_name} code found", ha='center', va='center',
                   transform=ax.transAxes, fontsize=12, color='gray')
            ax.set_title(lang_name, fontsize=12, fontweight='bold')
            continue
        
        data = np.array([layer_data[label] for label in labels])
        colors = [era_colors[label] for label in labels]
        
        if x_axis_type == "date" and isinstance(x_values[0], datetime):
            ax.stackplot(x_values, data, labels=labels, colors=colors, alpha=0.8)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        else:
            x_indices = list(range(len(x_values)))
            ax.stackplot(x_indices, data, labels=labels, colors=colors, alpha=0.8)
        
        # Calculate total lines for subtitle
        total_lines = sum(sum(layer_data[era]) for era in labels) // len(x_values) if x_values else 0
        final_lines = sum(layer_data[era][-1] for era in labels) if labels and layer_data[labels[0]] else 0
        
        ax.set_title(f"{lang_name} ({final_lines:,} lines)", fontsize=12, fontweight='bold')
        ax.set_ylabel("Lines of Code", fontsize=10)
        ax.grid(True, alpha=0.3, linestyle='--')
        ax.legend(loc='upper left', fontsize=8)
    
    # Set x-label on bottom plot only
    if x_axis_type == "commit":
        axes[-1].set_xlabel("Sample Point", fontsize=12)
    else:
        axes[-1].set_xlabel("Date", fontsize=12)
        plt.setp(axes[-1].xaxis.get_majorticklabels(), rotation=45, ha='right')
    
    fig.suptitle("Repository Geological History by Language", fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Chart saved to: {output_path}")
    else:
        plt.show()


def plot_geology(x_values, layer_data, x_axis_type: str, output_path: str = None):
    """
    Create a stacked area chart showing code geology.
    
    Args:
        x_values: Array of x-axis values (dates or indices)
        layer_data: Dict mapping release names to their values at each x point
        x_axis_type: Either "commit" or "date"
        output_path: If provided, save to this file instead of displaying
    """
    fig, ax = plt.subplots(figsize=(14, 7))
    
    # Prepare data for stacked area chart - filter out empty eras
    labels = [k for k, v in layer_data.items() if sum(v) > 0]
    data = np.array([layer_data[label] for label in labels])
    
    # Create stacked area chart
    cmap = plt.cm.tab20
    colors = [cmap(i % 20 / 20) for i in range(len(labels))]
    
    if x_axis_type == "date" and isinstance(x_values[0], datetime):
        ax.stackplot(x_values, data, labels=labels, colors=colors, alpha=0.8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        plt.xticks(rotation=45, ha='right')
    else:
        # Use indices for x-axis
        x_indices = list(range(len(x_values)))
        ax.stackplot(x_indices, data, labels=labels, colors=colors, alpha=0.8)
    
    # Customize the chart
    ax.set_title("Repository Geological History", fontsize=14, fontweight='bold')
    
    if x_axis_type == "commit":
        ax.set_xlabel("Sample Point", fontsize=12)
    else:
        ax.set_xlabel("Date", fontsize=12)
    
    ax.set_ylabel("Lines of Code", fontsize=12)
    
    # Add legend outside the plot
    ax.legend(loc='upper left', title="Release Era", bbox_to_anchor=(1.02, 1))
    
    # Add grid for readability
    ax.grid(True, alpha=0.3, linestyle='--')
    
    # Tight layout
    plt.tight_layout()
    
    # Save or display
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Chart saved to: {output_path}")
    else:
        plt.show()


def main():
    """Main entry point."""
    args = parse_args()
    
    # Validate the repository path
    repo_path = validate_repo_path(args.repo_path)
    print(f"Analyzing repository: {repo_path}")
    
    # Analyze repository to find release cut points
    main_branch, releases = analyze_repository(
        repo_path, 
        args.release_pattern,
        args.main_branch,
        args.initial_commit
    )
    
    if args.facet_languages:
        # Faceted analysis by language group
        print("\nRunning faceted analysis by language group...")
        facet_data = {}
        x_values = None
        
        for lang_name, extensions in LANGUAGE_GROUPS.items():
            print(f"\n{'='*60}")
            print(f"Analyzing {lang_name} files: {', '.join(extensions)}")
            print('='*60)
            
            # Check cache for this language group
            cache_path = get_cache_path(repo_path, args.cache_dir, extensions, args.release_pattern)
            cached = None if args.no_cache else load_cache(cache_path, releases)
            
            if cached:
                dates, layer_data, _, _ = cached
            elif args.cache_only:
                print(f"  ERROR: No cache available for {lang_name} and --cache-only specified")
                sys.exit(1)
            else:
                dates, layer_data = compute_geology(
                    repo_path,
                    main_branch,
                    releases,
                    extensions
                )
                # Save to cache
                save_cache(cache_path, dates, layer_data, releases, main_branch)
            
            facet_data[lang_name] = layer_data
            
            if x_values is None:
                x_values = dates
        
        if args.x_axis == "date":
            x_axis_type = "date"
        else:
            x_values = list(range(len(x_values))) if x_values else []
            x_axis_type = "commit"
        
        # Plot faceted chart
        plot_geology_faceted(x_values, facet_data, x_axis_type, args.output)
    else:
        # Check cache
        cache_path = get_cache_path(repo_path, args.cache_dir, args.extensions, args.release_pattern)
        cached = None if args.no_cache else load_cache(cache_path, releases)
        
        if cached:
            dates, layer_data, _, _ = cached
        elif args.cache_only:
            print(f"  ERROR: No cache available and --cache-only specified")
            sys.exit(1)
        else:
            # Compute actual geology (single chart)
            dates, layer_data = compute_geology(
                repo_path,
                main_branch,
                releases,
                args.extensions
            )
            # Save to cache
            save_cache(cache_path, dates, layer_data, releases, main_branch)
        
        if args.x_axis == "date":
            x_values = dates
            x_axis_type = "date"
        else:
            x_values = list(range(len(dates)))
            x_axis_type = "commit"
        
        # Plot the geology chart
        plot_geology(x_values, layer_data, x_axis_type, args.output)


if __name__ == "__main__":
    main()

