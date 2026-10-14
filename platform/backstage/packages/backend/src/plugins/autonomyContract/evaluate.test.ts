import { resolveAutonomyLevel, UnsupportedTargetingShapeError } from './evaluate';

// The exact shape deploy/platform/openfeature/featureflag.yaml ships,
// reproduced here rather than read off disk -- this module must resolve
// what a FeatureFlag's `spec.flagSpec.flags['autonomy-level']` object looks
// like on the wire, not what one YAML file happens to say today.
const SETTLEMENT_AGENT =
  'spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent';
const REVIEWER_UI =
  'spiffe://northstar.local/ns/northstar-agents/sa/reviewer-ui';

function shippedFlagSpec(thenBranch: string, elseBranch: string) {
  return {
    state: 'ENABLED',
    variants: { assisted: 'assisted', autonomous: 'autonomous' },
    defaultVariant: 'assisted',
    targeting: {
      if: [
        { in: [{ var: 'spiffeId' }, [SETTLEMENT_AGENT]] },
        thenBranch,
        elseBranch,
      ],
    },
  };
}

describe('resolveAutonomyLevel', () => {
  it('resolves the named caller to the then-branch (shipped state)', () => {
    const spec = shippedFlagSpec('assisted', 'assisted');
    expect(resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toEqual({
      level: 'assisted',
      branch: 'targeting-then',
    });
  });

  it('resolves an unnamed caller to the else-branch', () => {
    const spec = shippedFlagSpec('assisted', 'assisted');
    expect(resolveAutonomyLevel(spec, REVIEWER_UI)).toEqual({
      level: 'assisted',
      branch: 'targeting-else',
    });
  });

  it('reflects a live flip of the then-branch for the named caller only', () => {
    const spec = shippedFlagSpec('autonomous', 'assisted');
    expect(resolveAutonomyLevel(spec, SETTLEMENT_AGENT).level).toBe(
      'autonomous',
    );
    expect(resolveAutonomyLevel(spec, REVIEWER_UI).level).toBe(
      'assisted',
    );
  });

  it('fails closed to the PDS restrictive constant, NOT defaultVariant, when the flag is DISABLED', () => {
    // flagd errors on DISABLED (FLAG_NOT_FOUND), and the PDS's own
    // caller-supplied default (RESTRICTIVE_LEVEL) is what actually
    // resolves -- never this flag's defaultVariant. defaultVariant is
    // deliberately 'autonomous' here, NOT 'assisted': echoing defaultVariant
    // instead of the restrictive constant would return 'autonomous' and
    // this assertion would catch it.
    const spec = {
      ...shippedFlagSpec('autonomous', 'autonomous'),
      defaultVariant: 'autonomous',
      state: 'DISABLED',
    };
    expect(resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toEqual({
      level: 'assisted',
      branch: 'flag-disabled',
    });
  });

  it('refuses when the targeting branch names a variant flagd would not resolve either', () => {
    // The targeting branch's variant NAME is checked against `variants`
    // rather than echoed verbatim. flagd looks the name up and errors if
    // it is not declared (and the PDS then denies) -- a renamed/typo'd
    // branch must not be displayed as if it were a real, PDS-enforced
    // level.
    const spec = {
      state: 'ENABLED',
      variants: { assisted: 'assisted', autonomous: 'autonomous' },
      defaultVariant: 'autonomous',
      targeting: {
        if: [
          { in: [{ var: 'spiffeId' }, [SETTLEMENT_AGENT]] },
          'a-renamed-variant-that-is-not-declared',
          'assisted',
        ],
      },
    };
    expect(() => resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toThrow(
      UnsupportedTargetingShapeError,
    );
  });

  it('falls back to defaultVariant when there is no targeting block at all', () => {
    const spec = {
      state: 'ENABLED',
      variants: { assisted: 'assisted', autonomous: 'autonomous' },
      defaultVariant: 'assisted',
    };
    expect(resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toEqual({
      level: 'assisted',
      branch: 'default-variant',
    });
  });

  it('refuses to guess at a targeting rule using an operator other than "in"', () => {
    const spec = {
      state: 'ENABLED',
      defaultVariant: 'assisted',
      targeting: { if: [{ '==': [{ var: 'spiffeId' }, SETTLEMENT_AGENT] }, 'autonomous', 'assisted'] },
    };
    expect(() => resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toThrow(
      UnsupportedTargetingShapeError,
    );
  });

  it('refuses to guess at a nested/compound condition', () => {
    const spec = {
      state: 'ENABLED',
      defaultVariant: 'assisted',
      targeting: {
        if: [
          { and: [{ in: [{ var: 'spiffeId' }, [SETTLEMENT_AGENT]] }, true] },
          'autonomous',
          'assisted',
        ],
      },
    };
    expect(() => resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toThrow(
      UnsupportedTargetingShapeError,
    );
  });

  it('refuses a targeting.if with a wrong number of entries', () => {
    const spec = {
      state: 'ENABLED',
      defaultVariant: 'assisted',
      targeting: { if: [{ in: [{ var: 'spiffeId' }, [SETTLEMENT_AGENT]] }, 'assisted'] },
    };
    expect(() => resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toThrow(
      UnsupportedTargetingShapeError,
    );
  });

  it('refuses when defaultVariant is missing', () => {
    const spec = { state: 'ENABLED' };
    expect(() => resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toThrow(
      UnsupportedTargetingShapeError,
    );
  });

  it('refuses when defaultVariant names a variant that is not declared in variants', () => {
    // The sibling of the "refuses when the targeting branch names a variant
    // flagd would not resolve either" test above, but for the NO-TARGETING
    // path: an absent/null targeting block must not return defaultVariant
    // verbatim without consulting `variants`, or a typo'd defaultVariant
    // would display as a real level while flagd/the PDS would refuse it.
    // No `targeting` key at all, so this can only go red through the
    // default-variant path.
    const spec = {
      state: 'ENABLED',
      variants: { assisted: 'assisted', autonomous: 'autonomous' },
      defaultVariant: 'autonomous-TYPO',
    };
    expect(() => resolveAutonomyLevel(spec, SETTLEMENT_AGENT)).toThrow(
      UnsupportedTargetingShapeError,
    );
  });
});
