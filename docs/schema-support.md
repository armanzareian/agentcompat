# JSON Schema Support

AgentCompat implements a bounded JSON Schema subset for deterministic tool-call replay. The
inventory below is checked against the executable validator constants so newly supported
keywords cannot be omitted from this document.

## Assertion and applicator keywords

| Area | Supported keywords |
| --- | --- |
| Core | `$ref`, `$defs`, `definitions`, `type`, `enum`, `const` |
| Composition | `allOf`, `anyOf`, `oneOf`, `if`, `then`, `else` |
| Objects | `properties`, `required`, `additionalProperties`, `minProperties`, `maxProperties` |
| Arrays | `items`, `prefixItems`, `additionalItems`, `minItems`, `maxItems`, `uniqueItems` |
| Strings | `minLength`, `maxLength`, `pattern`, `format` |
| Numbers | `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf` |

Boolean schemas are supported at nested locations. Both Draft 2020-12 `prefixItems` tuples and
legacy array-valued `items` tuples are accepted.

## Formats

Format checks use Python's standard library and cover `date`, `date-time`, `time`, `email`,
`hostname`, `ipv4`, `ipv6`, `uri`, and `uuid`. `date-time` requires an RFC 3339-style `T`
separator and timezone. Format validation is intentionally stricter than JSON Schema's default
annotation-only behavior because compatibility replay must not silently ignore constraints.

## References

`$ref` supports JSON Pointer fragments in the current bundle and files beneath the bundle's
directory. Reference paths are resolved before replay. Absolute paths, remote URLs, paths that
escape the bundle directory, missing pointers, non-schema targets, and cycles are rejected as
input errors.

Sibling keywords next to `$ref` are applied with the referenced schema, matching modern JSON
Schema behavior.

## Annotations and extensions

The following annotations do not affect validation: `$comment`, `$schema`, `title`,
`description`, `default`, `examples`, `deprecated`, `readOnly`, `writeOnly`,
`contentEncoding`, and `contentMediaType`.

Provider extension keys beginning with `x-` are also treated as annotations.

## Unsupported semantics

Every other encountered schema keyword is reported by `agentcompat audit` and causes
`agentcompat check` to return input-error exit code `2` before calculating a score. This
includes currently unsupported behavior such as `contains`, `minContains`, `maxContains`,
`patternProperties`, `dependentRequired`, `dependentSchemas`, `not`, `propertyNames`,
`unevaluatedProperties`, `unevaluatedItems`, `$id`, and `$anchor`.

Run an audit before replay:

```bash
agentcompat audit --schema tools.json
agentcompat audit --schema tools.json --format json
```

An audit exits `0` when all encountered semantics are supported and `1` when it finds an
unsupported keyword.
