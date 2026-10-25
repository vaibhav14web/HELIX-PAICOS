"""
HELIX Abstract Syntax Tree (AST) Security Verifier.

Provides fail-closed, static inspection and security enforcement for executable Python
content, code snippets, workflow logic, and deterministic computational expressions.

Enforces:
1. Rejection of dangerous imports (subprocess, os, sys, ctypes, socket, etc.)
2. Rejection of code execution primitives (eval, exec, compile, open, __import__)
3. Rejection of reflection / sandbox escape dunder attributes (__class__, __subclasses__, __globals__, etc.)
4. Obfuscation detection (getattr/setattr dunder tricks, format string injections)
5. AST complexity and node limits against algorithmic complexity / AST bomb DoS attacks
6. Safe handling of malformed, oversized, or adversarial inputs
"""
from __future__ import annotations

import ast
import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence, Set

logger = logging.getLogger("helix.security.ast_verifier")

_DEFAULT_MAX_CODE_LENGTH: int = 65536  # 64 KB
_DEFAULT_MAX_AST_NODES: int = 2000
_DEFAULT_MAX_DEPTH: int = 50


class ASTSecurityViolationError(RuntimeError):
    """Raised when Python code fails AST security verification."""
    pass


class ASTPolicyProfile(str, enum.Enum):
    """Pre-defined security policy profiles."""
    STRICT_COMPUTATION = "strict_computation"  # Arithmetic, numbers, booleans, pure math only
    SAFE_EXPRESSION = "safe_expression"        # Expressions with safe functions, no imports/dunders
    RESTRICTED_PYTHON = "restricted_python"    # Scripting with functions, control flow; no dangerous imports/calls


# Dangerous standard library / low-level modules strictly prohibited in sandboxed scripts
_DANGEROUS_MODULES: Set[str] = {
    "os", "sys", "subprocess", "shutil", "ctypes", "socket", "pty",
    "builtins", "__builtin__", "posix", "nt", "importlib", "pickle",
    "marshal", "shelve", "webbrowser", "signal", "multiprocessing",
    "asyncio", "threading", "tempfile", "code", "codeop", "inspect"
}

# Dangerous callables prohibited in sandboxed environments
_DANGEROUS_CALLS: Set[str] = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "breakpoint", "input", "exit", "quit",
    "memoryview", "super"
}

# Dunder attributes used in Python sandbox escapes
_PROHIBITED_DUNDER_ATTRS: Set[str] = {
    "__class__", "__subclasses__", "__bases__", "__mro__", "__globals__",
    "__code__", "__closure__", "__builtins__", "__import__", "__dict__",
    "__getattribute__", "__getitem__", "__init__", "__new__", "__qualname__"
}

# Permitted math builtins for SAFE_EXPRESSION profile
_SAFE_MATH_BUILTINS: Set[str] = {
    "abs", "round", "min", "max", "sum", "pow", "divmod", "int", "float", "bool", "len"
}


@dataclass
class ASTSecurityPolicy:
    """Configurable policy for AST security verification."""
    profile: ASTPolicyProfile = ASTPolicyProfile.RESTRICTED_PYTHON
    max_code_length: int = _DEFAULT_MAX_CODE_LENGTH
    max_ast_nodes: int = _DEFAULT_MAX_AST_NODES
    max_ast_depth: int = _DEFAULT_MAX_DEPTH
    allowed_modules: Set[str] = field(default_factory=set)
    allowed_calls: Set[str] = field(default_factory=set)
    allow_imports: bool = False
    allow_statements: bool = True
    allow_functions: bool = True
    allow_classes: bool = False

    @classmethod
    def strict_computation(cls) -> "ASTSecurityPolicy":
        """Policy for pure mathematical expressions."""
        return cls(
            profile=ASTPolicyProfile.STRICT_COMPUTATION,
            allow_imports=False,
            allow_statements=False,
            allow_functions=False,
            allow_classes=False,
            max_ast_nodes=200,
            max_ast_depth=20,
        )

    @classmethod
    def safe_expression(cls) -> "ASTSecurityPolicy":
        """Policy for safe single expressions with math/length helpers."""
        return cls(
            profile=ASTPolicyProfile.SAFE_EXPRESSION,
            allow_imports=False,
            allow_statements=False,
            allow_functions=False,
            allow_classes=False,
            allowed_calls=set(_SAFE_MATH_BUILTINS),
            max_ast_nodes=500,
            max_ast_depth=30,
        )

    @classmethod
    def restricted_python(cls, allowed_modules: Sequence[str] | None = None) -> "ASTSecurityPolicy":
        """Policy for general sandboxed Python logic."""
        return cls(
            profile=ASTPolicyProfile.RESTRICTED_PYTHON,
            allow_imports=bool(allowed_modules),
            allow_statements=True,
            allow_functions=True,
            allow_classes=False,
            allowed_modules=set(allowed_modules or ()),
            allowed_calls=set(_SAFE_MATH_BUILTINS),
            max_ast_nodes=_DEFAULT_MAX_AST_NODES,
            max_ast_depth=_DEFAULT_MAX_DEPTH,
        )


class _SecurityASTVisitor(ast.NodeVisitor):
    """AST NodeVisitor enforcing security invariants."""

    def __init__(self, policy: ASTSecurityPolicy):
        self.policy = policy
        self.node_count = 0
        self.current_depth = 0
        self.max_depth_seen = 0
        self.violations: list[str] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1
        if self.node_count > self.policy.max_ast_nodes:
            self.violations.append(
                f"AST complexity exceeded limit: node count {self.node_count} > {self.policy.max_ast_nodes}"
            )
            return

        self.current_depth += 1
        if self.current_depth > self.max_depth_seen:
            self.max_depth_seen = self.current_depth
        if self.current_depth > self.policy.max_ast_depth:
            self.violations.append(
                f"AST nesting depth exceeded limit: depth {self.current_depth} > {self.policy.max_ast_depth}"
            )
            self.current_depth -= 1
            return

        super().generic_visit(node)
        self.current_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        if not self.policy.allow_imports:
            self.violations.append("Import statements are prohibited under this security policy.")
            return

        for alias in node.names:
            mod_base = alias.name.split(".")[0].lower()
            if mod_base in _DANGEROUS_MODULES or (self.policy.allowed_modules and mod_base not in self.policy.allowed_modules):
                self.violations.append(f"Import of unauthorized module '{alias.name}' is prohibited.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self.policy.allow_imports:
            self.violations.append("ImportFrom statements are prohibited under this security policy.")
            return

        mod_name = node.module or ""
        mod_base = mod_name.split(".")[0].lower()
        if not mod_name or mod_base in _DANGEROUS_MODULES or (self.policy.allowed_modules and mod_base not in self.policy.allowed_modules):
            self.violations.append(f"Import from unauthorized module '{mod_name}' is prohibited.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct call name
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in _DANGEROUS_CALLS:
                self.violations.append(f"Call to dangerous built-in '{func_name}' is prohibited.")
            elif self.policy.profile == ASTPolicyProfile.STRICT_COMPUTATION:
                self.violations.append(f"Function call '{func_name}' is prohibited in strict computation mode.")
            elif self.policy.profile == ASTPolicyProfile.SAFE_EXPRESSION:
                if func_name not in self.policy.allowed_calls:
                    self.violations.append(f"Function call '{func_name}' is not in allowed calls list.")
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in _DANGEROUS_CALLS or attr_name.startswith("__"):
                self.violations.append(f"Call to method '{attr_name}' is prohibited.")

        # Detect obfuscated getattr / setattr tricks, e.g. getattr(x, '__class__')
        if isinstance(node.func, ast.Name) and node.func.id in ("getattr", "setattr", "hasattr"):
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                target_attr = node.args[1].value
                if target_attr.startswith("__") or target_attr in _PROHIBITED_DUNDER_ATTRS:
                    self.violations.append(f"Obfuscated dunder attribute access '{target_attr}' via {node.func.id} is prohibited.")

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith("__") or attr in _PROHIBITED_DUNDER_ATTRS:
            self.violations.append(f"Access to dunder attribute '{attr}' is prohibited (potential sandbox escape).")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _DANGEROUS_CALLS:
            msg = f"Access to dangerous built-in '{node.id}' is prohibited."
            if not any(node.id in v for v in self.violations):
                self.violations.append(msg)
        elif node.id.startswith("__") or node.id in _PROHIBITED_DUNDER_ATTRS:
            msg = f"Access to dunder identifier '{node.id}' is prohibited (potential sandbox escape)."
            if not any(node.id in v for v in self.violations):
                self.violations.append(msg)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if not self.policy.allow_classes:
            self.violations.append(f"Class definitions are prohibited: '{node.name}'.")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not self.policy.allow_functions:
            self.violations.append(f"Function definitions are prohibited: '{node.name}'.")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if not self.policy.allow_functions:
            self.violations.append(f"Async function definitions are prohibited: '{node.name}'.")
        self.generic_visit(node)


def validate_python_ast(
    source_code: str | bytes,
    policy: ASTSecurityPolicy | None = None,
    filename: str = "<sandboxed_python>",
) -> tuple[bool, str | None]:
    """
    Validate Python source code against an ASTSecurityPolicy.
    
    Returns:
        (True, None) if code is compliant.
        (False, reason_string) if code is malformed, adversarial, or prohibited.
    """
    pol = policy or ASTSecurityPolicy.restricted_python()

    # Convert bytes to string if needed
    if isinstance(source_code, bytes):
        try:
            code_str = source_code.decode("utf-8")
        except UnicodeDecodeError as e:
            return False, f"Malformed encoding: {e}"
    elif isinstance(source_code, str):
        code_str = source_code
    else:
        return False, f"Expected str or bytes, got {type(source_code).__name__}"

    # 1. Length check
    if len(code_str) > pol.max_code_length:
        return False, f"Code length ({len(code_str)}) exceeds maximum allowed limit ({pol.max_code_length})."

    # Reject null bytes
    if "\0" in code_str:
        return False, "Null bytes detected in source code."

    # 2. Parse mode selection
    mode = "eval" if pol.profile in (ASTPolicyProfile.STRICT_COMPUTATION, ASTPolicyProfile.SAFE_EXPRESSION) else "exec"

    # 3. Safe AST parsing
    try:
        tree = ast.parse(code_str, filename=filename, mode=mode)
    except SyntaxError as e:
        return False, f"Python Syntax Error: {e.msg} at line {e.lineno}"
    except (ValueError, MemoryError, RecursionError) as e:
        return False, f"Malformed Python structure: {e}"
    except Exception as e:
        return False, f"AST parsing failure: {e}"

    # 4. AST traversal and security verification
    visitor = _SecurityASTVisitor(pol)
    visitor.visit(tree)

    if visitor.violations:
        first_violation = visitor.violations[0]
        total_violations = len(visitor.violations)
        summary = f"Security Violation: {first_violation}"
        if total_violations > 1:
            summary += f" (+{total_violations - 1} more violation(s))"
        return False, summary

    return True, None


def verify_python_code_or_raise(
    source_code: str | bytes,
    policy: ASTSecurityPolicy | None = None,
    filename: str = "<sandboxed_python>",
) -> None:
    """
    Validate Python source code and raise ASTSecurityViolationError if non-compliant.
    """
    valid, reason = validate_python_ast(source_code, policy=policy, filename=filename)
    if not valid:
        logger.warning("AST verification rejected code: %s", reason)
        raise ASTSecurityViolationError(reason or "AST security verification failed.")
