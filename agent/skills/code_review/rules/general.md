# General Code Review Rules

## Language-Agnostic Rules

### Error Handling
- Every external call (API, file I/O, DB) must have error handling
- Errors should not be silently swallowed (empty catch blocks)
- Error messages should be descriptive enough to diagnose the problem
- Resource cleanup must happen even when errors occur (try-finally / defer / using)

### Input Validation
- All external inputs (user input, API params, file content) must be validated
- Validate type, range, length, and format before use
- Never trust client-side validation alone

### Naming & Clarity
- Variable/function names should describe intent, not implementation
- Boolean variables should read as questions (isReady, hasPermission)
- Avoid abbreviations unless universally understood (e.g., URL, ID)

### Functions
- Each function should do one thing well
- Functions longer than 50 lines likely need splitting
- Avoid more than 3 levels of nesting — extract helper functions
- Return early to reduce nesting (guard clauses)

### Dependencies & Imports
- No unused imports
- No circular dependencies
- Pin dependency versions in production code
- Prefer standard library over third-party when functionality is equivalent

## Python-Specific Rules
- Use type hints for function signatures
- Use `pathlib.Path` over `os.path` string manipulation
- Use context managers (`with`) for resource management
- Prefer f-strings over `.format()` or `%` formatting

## Go-Specific Rules
- Always check error returns — never use `_` for errors in production code
- Use `context.Context` for cancellation propagation
- Close resources with `defer` immediately after creation
- Avoid naked returns in functions with named return values

## JavaScript/TypeScript-Specific Rules
- Use `const` by default, `let` only when reassignment is needed
- Avoid `any` type — use proper type definitions
- Use `async/await` over raw Promises for readability
- Handle Promise rejections — no unhandled promise rejections
