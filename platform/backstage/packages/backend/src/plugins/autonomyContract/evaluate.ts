/**
 * A narrow, honest reading of the `northstar-autonomy` FeatureFlag's
 * targeting rule -- NOT a JsonLogic engine.
 *
 * The card shows, per agent identity, the level the contract currently
 * resolves to, read live from the FeatureFlag, not from a cached copy. That
 * requires evaluating the targeting rule, because the rule is what decides
 * the resolved level per caller. flagd itself resolves this with a real
 * JsonLogic engine; this module does NOT reimplement one. It supports
 * exactly the one shape `deploy/platform/openfeature/featureflag.yaml`
 * uses --
 *
 *   { if: [ { in: [ {var: "spiffeId"}, [..ids..] ] }, thenVariant, elseVariant ] }
 *
 * -- and refuses, loudly, to guess at anything else. The alternative -- a
 * general evaluator -- would make this module a SECOND decision engine next
 * to flagd's, and the one thing this design will not tolerate is Backstage
 * acquiring a path to a decision that isn't "edit the resource the PDS
 * already reads". A narrow reader that refuses unknown shapes cannot drift
 * from the PDS's real answer without saying so; a permissive one could
 * silently show a level flagd would not actually resolve.
 *
 * Fails CLOSED on anything it cannot parse: an absent targeting block
 * resolves to `defaultVariant` (there is no rule to consult, so this is what
 * flagd itself would resolve too). A DISABLED flag does NOT fall back to
 * `defaultVariant` -- see RESTRICTIVE_LEVEL below: flagd treats a DISABLED
 * flag as an error, not as a reason to fall back to `defaultVariant`. A
 * targeting block that is present but not in the one shape above throws
 * rather than returning a guess, and so does a targeting branch naming a
 * variant `variants` does not declare.
 */

/**
 * The Policy Decision Service's own hardcoded fail-closed constant --
 * `RESTRICTIVE_LEVEL` in `platform/policy-decision-service/pds/autonomy.py`,
 * passed as the OpenFeature client's caller-supplied default and forced when
 * resolution errors. Reproduced here rather than imported: different
 * runtime (Python vs this Node plugin), same reason the SPIFFE literals in
 * `router.ts` are reproduced rather than imported.
 *
 * flagd treats a DISABLED flag as `FLAG_NOT_FOUND` -- an error, not
 * `defaultVariant`. The OpenFeature SDK then falls back to the CALLER's own
 * default, which for the PDS is this constant, not whatever `defaultVariant`
 * happens to say on the flag. A card that echoed `defaultVariant` for a
 * DISABLED flag could show `autonomous` while the PDS enforces `assisted` --
 * a lying UI in the permissive direction. So this reader shows the PDS's
 * real fallback here, not the flag's own `defaultVariant`.
 */
export const RESTRICTIVE_LEVEL = 'assisted';

export class UnsupportedTargetingShapeError extends Error {
  constructor(reason: string) {
    super(
      `northstar-autonomy's targeting rule is not the one shape this reader ` +
        `supports (an "in" check against a single caller list, two literal ` +
        `branches): ${reason}. Refusing to guess -- this module is a reader, ` +
        `not a second copy of flagd's JsonLogic engine.`,
    );
    this.name = 'UnsupportedTargetingShapeError';
  }
}

export interface AutonomyFlagSpec {
  state?: unknown;
  variants?: unknown;
  defaultVariant?: unknown;
  targeting?: unknown;
}

export interface ResolvedLevel {
  /** The variant string this identity resolves to right now. */
  level: string;
  /** Which part of the flag produced it -- for the card's own display, and
   * for tests to pin without re-deriving the targeting shape.
   * `flag-disabled` is deliberately distinct from `default-variant`: it
   * means "the PDS's restrictive fallback, because flagd would error here",
   * not "this flag's own configured default". */
  branch:
    | 'targeting-then'
    | 'targeting-else'
    | 'default-variant'
    | 'flag-disabled';
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * The one targeting shape this module reads. Returns the parsed
 * `{ callerIds, thenVariant, elseVariant }` or throws
 * `UnsupportedTargetingShapeError` naming exactly what did not match.
 */
function parseNarrowTargeting(targeting: unknown): {
  callerIds: string[];
  thenVariant: string;
  elseVariant: string;
} {
  if (!isPlainObject(targeting) || !Array.isArray(targeting.if)) {
    throw new UnsupportedTargetingShapeError(
      'targeting.if is not an array',
    );
  }
  const ifArray = targeting.if;
  if (ifArray.length !== 3) {
    throw new UnsupportedTargetingShapeError(
      `targeting.if has ${ifArray.length} entries, expected exactly 3 ` +
        '(condition, then-branch, else-branch)',
    );
  }
  const [condition, thenVariant, elseVariant] = ifArray;
  if (typeof thenVariant !== 'string' || typeof elseVariant !== 'string') {
    throw new UnsupportedTargetingShapeError(
      'the then/else branches are not both literal strings',
    );
  }
  if (!isPlainObject(condition) || !Array.isArray(condition.in)) {
    throw new UnsupportedTargetingShapeError(
      'targeting.if[0] is not an "in" JsonLogic operator',
    );
  }
  const inArgs = condition.in;
  if (inArgs.length !== 2) {
    throw new UnsupportedTargetingShapeError(
      `"in" takes 2 arguments, found ${inArgs.length}`,
    );
  }
  const [varRef, callerIds] = inArgs;
  if (
    !isPlainObject(varRef) ||
    varRef.var !== 'spiffeId' ||
    !Array.isArray(callerIds) ||
    !callerIds.every(id => typeof id === 'string')
  ) {
    throw new UnsupportedTargetingShapeError(
      '"in" is not checking {var: "spiffeId"} against a literal string list',
    );
  }
  return { callerIds, thenVariant, elseVariant };
}

/**
 * Resolve the level `autonomy-level` currently produces for `spiffeId`,
 * reading only the object this repo's one FeatureFlag has ever shipped.
 *
 * Throws `UnsupportedTargetingShapeError` if `flagSpec.targeting` is present
 * but not the narrow shape above -- this is the fail-closed behaviour that
 * keeps this reader from ever silently disagreeing with flagd.
 */
export function resolveAutonomyLevel(
  flagSpec: AutonomyFlagSpec,
  spiffeId: string,
): ResolvedLevel {
  const defaultVariant = flagSpec.defaultVariant;
  if (typeof defaultVariant !== 'string') {
    throw new UnsupportedTargetingShapeError(
      'defaultVariant is missing or not a string',
    );
  }

  if (flagSpec.state !== 'ENABLED') {
    // flagd errors (FLAG_NOT_FOUND) on a DISABLED flag; it does NOT resolve
    // to defaultVariant. The PDS's OpenFeature client then falls back to
    // ITS OWN caller-supplied default (RESTRICTIVE_LEVEL), never this flag's
    // defaultVariant -- so reporting defaultVariant here could show a level
    // the PDS would never actually enforce. Report the restrictive constant
    // under a branch label that says exactly that.
    return { level: RESTRICTIVE_LEVEL, branch: 'flag-disabled' };
  }

  // This check must run BEFORE the targeting branch is parsed: if it ran
  // after, the no-targeting path below would return `defaultVariant` -- a
  // variant NAME -- verbatim, never validated against `variants` at all.
  // flagd's `getStringValue` always returns the variant's VALUE, so an
  // undeclared/typo'd `defaultVariant` would show a level flagd would
  // refuse to resolve (and the PDS would then deny) -- the same lying-UI
  // shape guarded against on the targeting path below. Checking `variants`
  // once, here, before either path reads it, closes both at once.
  const variants = flagSpec.variants;
  if (!isPlainObject(variants)) {
    throw new UnsupportedTargetingShapeError(
      'variants is missing or not an object -- cannot confirm either ' +
        'defaultVariant or a targeting branch names a real variant',
    );
  }

  if (flagSpec.targeting === undefined || flagSpec.targeting === null) {
    const resolvedDefault = variants[defaultVariant];
    if (typeof resolvedDefault !== 'string') {
      throw new UnsupportedTargetingShapeError(
        `defaultVariant names variant "${defaultVariant}", which is not ` +
          'one of the flag\'s declared `variants` -- flagd would error ' +
          'resolving this (and the PDS would then deny), so this reader ' +
          'refuses to display it as a level rather than guess',
      );
    }
    return { level: resolvedDefault, branch: 'default-variant' };
  }

  const { callerIds, thenVariant, elseVariant } = parseNarrowTargeting(
    flagSpec.targeting,
  );

  const level = callerIds.includes(spiffeId) ? thenVariant : elseVariant;
  const branch: 'targeting-then' | 'targeting-else' = callerIds.includes(
    spiffeId,
  )
    ? 'targeting-then'
    : 'targeting-else';

  // The targeting branch's variant NAME is checked against `variants`
  // before being returned: flagd looks that name up and errors (and the
  // PDS then denies) if it is not a declared variant -- a renamed/typo'd
  // branch would make flagd/the PDS refuse while this card displayed the
  // bogus name as if it were a real level.
  const resolved = variants[level];
  if (typeof resolved !== 'string') {
    throw new UnsupportedTargetingShapeError(
      `the ${branch} branch names variant "${level}", which is not one of ` +
        'the flag\'s declared `variants` -- flagd would error resolving ' +
        'this (and the PDS would then deny), so this reader refuses to ' +
        'display it as a level rather than guess',
    );
  }

  return { level: resolved, branch };
}
