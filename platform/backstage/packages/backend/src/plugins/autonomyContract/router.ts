/**
 * The two HTTP routes the autonomy card calls, mounted at
 * `/api/autonomy-contract/*` by Backstage's httpRouter service (namespaced
 * by this plugin's own `pluginId`, the same convention every other backend
 * plugin here uses -- see `@backstage/plugin-kubernetes-backend`'s own
 * router for the pattern this file follows).
 *
 * `GET /status` -- read live, per identity.
 * `POST /variant` -- the one write this plugin can make: flip the targeting
 * `if[1]` branch between `assisted` and `autonomous`. Nothing else is
 * reachable through this router: no path parameter names a resource, no
 * request body field names a JSON Patch path, and the only two accepted
 * `variant` values are the flag's own two declared variants.
 */
import express, { Router } from 'express';
import type { AuditorService, LoggerService } from '@backstage/backend-plugin-api';
import {
  AUTONOMY_NAME,
  AUTONOMY_NAMESPACE,
  AutonomyContractClient,
  FIELD_MANAGER,
} from './k8sClient';
import {
  AutonomyFlagSpec,
  resolveAutonomyLevel,
  UnsupportedTargetingShapeError,
} from './evaluate';
import { emitContractChangeSpan } from './telemetry';

// The two identities meant by "per agent identity": the two SPIFFE IDs the
// settlement flow can present (deploy/platform/spire/
// registration.yaml for the junior's explicit ClusterSPIFFEID; the senior
// gets its identity from the chart's own fallback CR, same ns/sa template).
// Reproduced as literals rather than imported from a Python module -- same
// reason tests/harness/contract.py gives for reproducing its own
// TARGETED_CALLER rather than importing it: a different runtime, not a
// shared package.
export const TARGETED_IDENTITIES = {
  'settlement-agent':
    'spiffe://northstar.local/ns/northstar-agents/sa/settlement-agent',
  'senior-settlement-agent':
    'spiffe://northstar.local/ns/northstar-agents/sa/senior-settlement-agent',
} as const;

export const VALID_VARIANTS = ['assisted', 'autonomous'] as const;
export type Variant = (typeof VALID_VARIANTS)[number];

export function isValidVariant(value: unknown): value is Variant {
  return (
    typeof value === 'string' &&
    (VALID_VARIANTS as readonly string[]).includes(value)
  );
}

export function extractFlagSpec(raw: unknown): AutonomyFlagSpec {
  const flag = (raw as any)?.spec?.flagSpec?.flags?.['autonomy-level'];
  if (!flag || typeof flag !== 'object') {
    throw new Error(
      `${AUTONOMY_NAME} in ${AUTONOMY_NAMESPACE} has no ` +
        'spec.flagSpec.flags["autonomy-level"] -- got a shape this reader ' +
        'does not recognise as the autonomy contract at all',
    );
  }
  return flag as AutonomyFlagSpec;
}

/** The live `if[1]` literal, read fresh -- never assumed from a previous
 * response. Throws if it is not there or not a string, the same fail-closed
 * behaviour `evaluate.ts` uses for the rest of the targeting shape. */
export function readThenBranch(flagSpec: AutonomyFlagSpec): string {
  const targeting = flagSpec.targeting as any;
  const value = targeting?.if?.[1];
  if (typeof value !== 'string') {
    throw new UnsupportedTargetingShapeError(
      'targeting.if[1] (the one branch this endpoint ever patches) is ' +
        'missing or not a string',
    );
  }
  return value;
}

export interface RouterDeps {
  client: AutonomyContractClient;
  logger: LoggerService;
  auditor: AuditorService;
}

/**
 * Mounted instead of `createAutonomyContractRouter` when the writer
 * credential could not be acquired at startup (see `index.ts`'s
 * `initAutonomyContract`). Answers 503 with the reason on every route this
 * plugin owns, rather than an unguarded throw out of `registerInit` that
 * Backstage's `BackendInitializer` would turn into a fatal
 * `BackendStartupError`, taking the WHOLE backend down (scaffolder,
 * techdocs, catalog, everything) for a token only this one plugin needs. A
 * missing writer token degrades this plugin only.
 */
export function createDegradedAutonomyContractRouter(opts: {
  reason: string;
}): Router {
  const router = Router();
  router.use(express.json());
  router.use((_req, res) => {
    res.status(503).json({
      error:
        'autonomy-contract failed to initialize: the writer credential ' +
        'could not be acquired at startup',
      reason: opts.reason,
    });
  });
  return router;
}

export function createAutonomyContractRouter(deps: RouterDeps): Router {
  const { client, logger, auditor } = deps;
  const router = Router();
  router.use(express.json());

  router.get('/status', async (req, res) => {
    const auditorEvent = await auditor.createEvent({
      eventId: 'contract-read',
      severityLevel: 'low',
      request: req,
      meta: { flag: AUTONOMY_NAME, namespace: AUTONOMY_NAMESPACE },
    });
    try {
      const raw = await client.getFlag();
      const flagSpec = extractFlagSpec(raw);
      const perIdentity: Record<string, { level: string; branch: string }> = {};
      for (const [label, spiffeId] of Object.entries(TARGETED_IDENTITIES)) {
        perIdentity[label] = resolveAutonomyLevel(flagSpec, spiffeId);
      }
      res.json({
        flag: AUTONOMY_NAME,
        namespace: AUTONOMY_NAMESPACE,
        state: flagSpec.state,
        defaultVariant: flagSpec.defaultVariant,
        perIdentity,
      });
      auditorEvent
        .success()
        .catch(error => logger.error('failed to emit audit event contract-read', error));
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      await auditorEvent.fail({ error: err });
      logger.error('autonomy-contract GET /status failed', err);
      const status = err instanceof UnsupportedTargetingShapeError ? 502 : 500;
      res.status(status).json({ error: err.message });
    }
  });

  router.post('/variant', async (req, res) => {
    const requested = req.body?.variant;
    const auditorEvent = await auditor.createEvent({
      eventId: 'contract-write',
      severityLevel: 'high',
      request: req,
      meta: {
        flag: AUTONOMY_NAME,
        namespace: AUTONOMY_NAMESPACE,
        requestedVariant:
          typeof requested === 'string' ? requested : String(requested),
        fieldManager: FIELD_MANAGER,
      },
    });

    if (!isValidVariant(requested)) {
      const err = new Error(
        `request body's "variant" must be one of ${VALID_VARIANTS.join(
          ', ',
        )}; got ${JSON.stringify(requested)}`,
      );
      await auditorEvent.fail({ error: err });
      res.status(400).json({ error: err.message });
      return;
    }

    try {
      const before = extractFlagSpec(await client.getFlag());
      const previousVariant = readThenBranch(before);

      await client.patchThenBranch(requested);

      const emitted = await emitContractChangeSpan({
        previousVariant,
        newVariant: requested,
        fieldManager: FIELD_MANAGER,
        flagName: AUTONOMY_NAME,
        flagNamespace: AUTONOMY_NAMESPACE,
      });
      if (!emitted.ok) {
        // "100% telemetry, never sampled": a failed export is reported
        // loudly, never swallowed -- but it does not fail the request. The
        // k8s write already succeeded; the auditor event above and the
        // fieldManager on the object itself are still real even if this
        // third signal did not make it to the collector.
        logger.error(
          `autonomy-contract-write: OTLP span was NOT delivered (${
            emitted.error ?? 'unknown reason'
          }); the FeatureFlag write itself still succeeded`,
        );
      }

      res.json({
        previousVariant,
        newVariant: requested,
        telemetryEmitted: emitted.ok,
      });
      auditorEvent
        .success()
        .catch(error => logger.error('failed to emit audit event contract-write', error));
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      await auditorEvent.fail({ error: err });
      logger.error('autonomy-contract POST /variant failed', err);
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
