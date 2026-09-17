/**
 * A small, dependency-free glob matcher for the fnmatch-style patterns
 * ScopePolicy/ProjectExecutionProfile already document as their expected
 * syntax (see apps/api/app/agent_runtime/policies.py's ScopePolicy
 * docstring: "apps/api/app/services/**"). Deliberately minimal — supports
 * `*` (any run of characters except `/`), `**` (any run of characters
 * including `/`), and `?` (one character) — not a full glob spec, but
 * sufficient for every pattern this codebase's own fixtures/policies use.
 */
export function minimatch(input: string, pattern: string): boolean {
  const regex = globToRegExp(pattern);
  return regex.test(input);
}

function globToRegExp(pattern: string): RegExp {
  let out = "^";
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === "*") {
      if (pattern[i + 1] === "*") {
        out += ".*";
        i++;
      } else {
        out += "[^/]*";
      }
    } else if (c === "?") {
      out += "[^/]";
    } else if (".+^${}()|[]\\".includes(c ?? "")) {
      out += "\\" + c;
    } else {
      out += c;
    }
  }
  out += "$";
  return new RegExp(out);
}
