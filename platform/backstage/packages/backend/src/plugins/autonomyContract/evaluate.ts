/**
 * M6 Task 3: a narrow, honest reading of the `northstar-autonomy` FeatureFlag's
 * targeting rule -- NOT a JsonLogic engine.
 *
 * Design §4/§5.3: "the card shows, per agent identity, the level the contract
 * currently resolves ... read live from the FeatureFlag, not from a cached
 * copy." That requires evaluating the targeting rule, because the rule is
 * what decides the resolved level per caller. flagd itself resolves this with
 * a real JsonLogic engine; this module does NOT reimplement one. It supports
 * exactly the one shape `deploy/platform/openfeature/featureflag.yaml` has
 * ever used (ruling M5-R8's own targeting block) --
 *
 *   { if: [ { in: [ {var: "spiffeId"}, [..ids..] ] }, thenVariant, elseVariant ] }
 *
 * -- and refuses, loudly, to guess at anything else. The alternative -- a
 * general evaluator -- would make this module a SECOND decision engine next
 * to flagd's, and the one thing design §4 will not tolerate is Backstage
 * acquiring a path to a decision that isn't "edit the resource the PDS
 * already reads" (§4's own words). A narrow reader that refuses unknown shapes
 * cannot drift from the PDS's real answer without saying so; a permissive one
 * could silently show a level flagd would not actually resolve.
 *
 * Fails CLOSED on anything it cannot parse: an absent or DISABLED flag
 * resolves to `defaultVariant` (mirroring flagd's own fail-closed default,
 * ruling M5-R8), and a targeting block that is present but not in the one
 * shape above throws rather than returning a guess.
 */

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
   * for tests to pin without re-deriving the targeting shape. */
  branch: 'targeting-then' | 'targeting-else' | 'default-variant';
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
    // Fail closed, mirroring flagd's own DISABLED behaviour: no targeting is
    // ever consulted for a disabled flag.
    return { level: defaultVariant, branch: 'default-variant' };
  }

  if (flagSpec.targeting === undefined || flagSpec.targeting === null) {
    return { level: defaultVariant, branch: 'default-variant' };
  }

  const { callerIds, thenVariant, elseVariant } = parseNarrowTargeting(
    flagSpec.targeting,
  );

  if (callerIds.includes(spiffeId)) {
    return { level: thenVariant, branch: 'targeting-then' };
  }
  return { level: elseVariant, branch: 'targeting-else' };
}
