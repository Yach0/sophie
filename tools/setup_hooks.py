#!/usr/bin/env python3
"""Setup script for git hooks to support worktree dependency syncing."""

import argparse
import stat
import subprocess
import sys
from pathlib import Path


HOOK_TEMPLATE = """#!/bin/bash
#
# Git hook to sync dependencies when checking out a worktree
# This hook runs after git checkout operations, including worktree creation
#

set -e

# Get the top-level directory of the current worktree
WORKTREE_DIR="$(git rev-parse --show-toplevel)"

# Check if we're in a worktree (not the main repo)
GIT_DIR="$(git rev-parse --git-dir)"
IS_WORKTREE=false

# If .git is a file (not a directory), we're in a worktree
if [ -f "$GIT_DIR" ]; then
    IS_WORKTREE=true
fi

# Also check if this is a worktree by looking at git worktree list
if git worktree list --porcelain 2>/dev/null | grep -q "^worktree $WORKTREE_DIR$"; then
    # Check if this is NOT the main worktree
    if ! git rev-parse --git-common 2>/dev/null | grep -q "^$WORKTREE_DIR"; then
        IS_WORKTREE=true
    fi
fi

# Only run sync if we're in a worktree
if [ "$IS_WORKTREE" = true ]; then
    cd "$WORKTREE_DIR"
    make setup_worktree
fi

exit 0
"""


def get_git_common_dir() -> Path | None:
    """Get the git common directory (for worktrees) or git directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
        git_dir = Path(result.stdout.strip())
        if git_dir.exists():
            return git_dir.resolve()
    except subprocess.CalledProcessError:
        pass

    # Fallback to git directory
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
        git_dir = Path(result.stdout.strip())
        if git_dir.exists():
            return git_dir.resolve()
    except subprocess.CalledProcessError:
        pass

    return None


def install_hook(git_dir: Path, hook_name: str, force: bool = False) -> bool:
    """Install a git hook.

    Args:
        git_dir: Path to the git directory
        hook_name: Name of the hook to install
        force: Whether to overwrite existing hooks

    Returns:
        True if installed successfully, False otherwise
    """
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hooks_dir / hook_name

    if hook_path.exists() and not force:
        print(f"⚠️  Hook '{hook_name}' already exists at: {hook_path}")
        print("   Use --force to overwrite")
        return False

    # Write the hook
    hook_path.write_text(HOOK_TEMPLATE)

    # Make it executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"✅ Installed '{hook_name}' hook at: {hook_path}")
    return True


def uninstall_hook(git_dir: Path, hook_name: str) -> bool:
    """Uninstall a git hook.

    Args:
        git_dir: Path to the git directory
        hook_name: Name of the hook to uninstall

    Returns:
        True if uninstalled successfully, False otherwise
    """
    hooks_dir = git_dir / "hooks"
    hook_path = hooks_dir / hook_name

    if not hook_path.exists():
        print(f"⚠️  Hook '{hook_name}' not found at: {hook_path}")
        return False

    # Check if it's our hook (contains the worktree detection comment)
    content = hook_path.read_text()
    if "Worktree detected" not in content:
        print(f"⚠️  Hook '{hook_name}' exists but doesn't appear to be our hook")
        print("   Manual removal may be needed")
        return False

    hook_path.unlink()
    print(f"✅ Uninstalled '{hook_name}' hook from: {hook_path}")
    return True


def status(git_dir: Path) -> None:
    """Show the status of git hooks."""
    hooks_dir = git_dir / "hooks"

    print(f"Git directory: {git_dir}")
    print()

    if not hooks_dir.exists():
        print("No hooks directory found")
        return

    # Check for our hooks
    worktree_hooks = ["post-checkout"]

    for hook_name in worktree_hooks:
        hook_path = hooks_dir / hook_name
        if hook_path.exists():
            content = hook_path.read_text()
            is_our_hook = "Worktree detected" in content
            status = "✅ installed (ours)" if is_our_hook else "⚠️  installed (custom)"
        else:
            status = "❌ not installed"

        print(f"  {hook_name}: {status}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Setup git hooks for worktree dependency syncing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Install hooks
  %(prog)s install

  # Force reinstall hooks
  %(prog)s install --force

  # Uninstall hooks
  %(prog)s uninstall

  # Check status
  %(prog)s status
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Install command
    install_parser = subparsers.add_parser("install", help="Install git hooks")
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing hooks",
    )

    # Uninstall command
    subparsers.add_parser("uninstall", help="Uninstall git hooks")

    # Status command
    subparsers.add_parser("status", help="Show hook status")

    args = parser.parse_args()

    # Find git directory
    git_dir = get_git_common_dir()
    if git_dir is None:
        print("❌ Error: Not in a git repository")
        return 1

    if args.command == "install":
        success = install_hook(git_dir, "post-checkout", force=args.force)
        if success:
            print()
            print("🎉 Git hooks installed successfully!")
            print("   Dependencies will be synced automatically when creating new worktrees.")
        return 0 if success else 1

    elif args.command == "uninstall":
        success = uninstall_hook(git_dir, "post-checkout")
        if success:
            print()
            print("🗑️  Git hooks uninstalled successfully!")
        return 0 if success else 1

    elif args.command == "status":
        status(git_dir)
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
