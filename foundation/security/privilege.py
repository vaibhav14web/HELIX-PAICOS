import sys
import ctypes
import logging

logger = logging.getLogger("helix.security.privilege")


def is_admin() -> bool:
    """Check if the current process is running with elevated Administrator privileges."""
    if sys.platform == "win32":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception as e:
            logger.warning("Failed to check admin status: %s", e)
            return False
    try:
        import os
        return os.geteuid() == 0
    except AttributeError:
        return False


def check_privilege_boundary() -> dict[str, bool | str]:
    """Verify that HELIX main process is running as a restricted user."""
    admin_status = is_admin()
    if admin_status:
        logger.warning(
            "SECURITY WARNING: HELIX process is running with Administrator elevation! "
            "HELIX should run as a standard restricted user ('asInvoker')."
        )
    return {
        "is_admin": admin_status,
        "execution_level": "asInvoker" if not admin_status else "requireAdministrator",
        "restricted": not admin_status,
    }
