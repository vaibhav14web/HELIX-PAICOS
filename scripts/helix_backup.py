#!/usr/bin/env python3
"""
Command-Line Interface for HELIX Portable Backup and Restore.

Supports:
- create: Create an atomic, verified backup archive for a specified user.
- inspect: View the manifest and metadata of an archive without extraction.
- validate: Run preflight cryptographic and security checks on an archive.
- restore: Transactionally restore a validated backup to a user partition (requires --confirm).
"""
import argparse
import json
import sys
from pathlib import Path

# Ensure HELIX repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from foundation.storage_manager.backup_restore import (
    get_backup_restore_manager,
    BackupError,
    BackupAuthorizationError,
)


def cmd_create(args: argparse.Namespace) -> int:
    mgr = get_backup_restore_manager()
    print(f"Creating backup for user '{args.user_id}'...")
    res = mgr.create_backup(
        user_id=args.user_id,
        destination_path=args.out,
        include_categories=args.include,
        exclude_categories=args.exclude,
        comment=args.comment or "",
    )
    if res.success:
        print(f"Backup created successfully:")
        print(f"  Archive: {res.archive_path}")
        print(f"  Backup ID: {res.backup_id}")
        print(f"  Files: {res.total_files} ({res.total_bytes} bytes)")
        if res.manifest:
            print(f"  Categories: {', '.join(res.manifest.included_categories)}")
        return 0
    else:
        print(f"Backup creation failed: {res.error}", file=sys.stderr)
        return 1


def cmd_inspect(args: argparse.Namespace) -> int:
    mgr = get_backup_restore_manager()
    try:
        manifest = mgr.inspect_backup(args.archive)
        print("Backup Archive Manifest:")
        print(f"  Backup ID:         {manifest.backup_id}")
        print(f"  Format Version:    {manifest.format_version}")
        print(f"  HELIX Version:     {manifest.helix_version}")
        print(f"  Created At (UTC):  {manifest.created_at}")
        print(f"  Source User:       {manifest.source_user_id}")
        print(f"  Source OS:         {manifest.source_os}")
        print(f"  Included Categories: {', '.join(manifest.included_categories)}")
        print(f"  Total Files:       {len(manifest.file_entries)}")
        print(f"  Secrets Excluded:  {manifest.system_secrets_excluded}")
        if manifest.comment:
            print(f"  Comment:           {manifest.comment}")
        return 0
    except BackupError as e:
        print(f"Failed to inspect backup: {e}", file=sys.stderr)
        return 1


def cmd_validate(args: argparse.Namespace) -> int:
    mgr = get_backup_restore_manager()
    print(f"Validating archive: {args.archive}...")
    report = mgr.validate_backup(args.archive, target_user_id=args.user_id)
    print(f"Validation Result: {'VALID' if report.valid else 'INVALID'}")
    print(f"  Backup ID:      {report.backup_id}")
    print(f"  Format Version: {report.format_version}")
    print(f"  Source User:    {report.source_user_id}")
    print(f"  Total Files:    {report.total_files} ({report.total_bytes} bytes)")
    print(f"  Categories:     {', '.join(report.categories)}")
    print(f"  Encryption:     {'Compatible' if report.encryption_compatible else 'Incompatible / Machine-Bound'}")

    if report.warnings:
        print("\nWarnings:")
        for w in report.warnings:
            print(f"  [WARN] {w}")

    if report.errors:
        print("\nErrors:")
        for err in report.errors:
            print(f"  [ERR]  {err}", file=sys.stderr)
        return 1

    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    mgr = get_backup_restore_manager()
    if not args.confirm:
        print("ERROR: Restore will modify or overwrite user state.", file=sys.stderr)
        print("Pass '--confirm' to confirm authorization for this restore.", file=sys.stderr)
        return 2

    print(f"Initiating restore for user '{args.user_id}' from {args.archive}...")
    try:
        res = mgr.restore_backup(
            archive_path=args.archive,
            target_user_id=args.user_id,
            confirmation=True,
        )
        if res.success:
            print("Restore completed successfully:")
            print(f"  Target User:        {res.target_user_id}")
            print(f"  Files Restored:     {res.restored_files_count}")
            print(f"  Categories:         {', '.join(res.restored_categories)}")
            print(f"  Destination Root:   {res.destination_root}")
            return 0
        else:
            print(f"Restore failed: {res.error}", file=sys.stderr)
            if res.rollback_performed:
                print("Rollback was performed: previous user state was safely restored.", file=sys.stderr)
            return 1
    except BackupAuthorizationError as auth_err:
        print(f"Authorization error: {auth_err}", file=sys.stderr)
        return 2
    except BackupError as bkp_err:
        print(f"Restore failed: {bkp_err}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="HELIX Portable Backup and Restore Tool")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # create
    p_create = subparsers.add_parser("create", help="Create a user backup archive")
    p_create.add_argument("--user-id", default="default_owner", help="User ID to backup")
    p_create.add_argument("--out", help="Destination path or directory")
    p_create.add_argument("--include", nargs="+", help="Explicit categories to include")
    p_create.add_argument("--exclude", nargs="+", help="Categories to exclude")
    p_create.add_argument("--comment", help="Optional comment to record in manifest")

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect a backup archive manifest")
    p_inspect.add_argument("--archive", required=True, help="Path to backup archive")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate archive integrity and safety")
    p_validate.add_argument("--archive", required=True, help="Path to backup archive")
    p_validate.add_argument("--user-id", default="default_owner", help="Target user ID")

    # restore
    p_restore = subparsers.add_parser("restore", help="Restore backup to user partition")
    p_restore.add_argument("--archive", required=True, help="Path to backup archive")
    p_restore.add_argument("--user-id", required=True, help="Destination user ID")
    p_restore.add_argument("--confirm", action="store_true", help="Confirm authorization to apply restore")

    args = parser.parse_args()

    if args.subcommand == "create":
        return cmd_create(args)
    elif args.subcommand == "inspect":
        return cmd_inspect(args)
    elif args.subcommand == "validate":
        return cmd_validate(args)
    elif args.subcommand == "restore":
        return cmd_restore(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
