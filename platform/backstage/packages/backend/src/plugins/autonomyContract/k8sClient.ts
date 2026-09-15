/**
 * M6 Task 3: talks to the `northstar-autonomy` FeatureFlag, and to nothing
 * else -- deliberately. Every identifier below (group/version/plural/
 * namespace/name/JSON-Patch path) is a literal constant in THIS file, never
 * something a request body supplies. That is the app-layer half of design
 * §4's "resourceNames: [northstar-autonomy] -- not `*`": even if the RBAC
 * Role in deploy/k8s/backstage.yaml were ever loosened by mistake, this
 * client still only ever asks the API server for one object, by one fixed
 * name, and only ever patches one fixed field on it.
 *
 * Uses the SECOND ServiceAccount's credential (backstage-autonomy-writer),
 * mounted at the paths below by deploy/k8s/backstage.yaml -- never the pod's
 * own default-mounted token, which is backstage-read-only's and cannot
 * patch anything (verified live: M6 Task 1's report, and this task's own
 * `kubectl auth can-i` matrix).
 */
import * as fs from 'fs';
import { CustomObjectsApi, KubeConfig } from '@kubernetes/client-node';

export const AUTONOMY_GROUP = 'core.openfeature.dev';
export const AUTONOMY_VERSION = 'v1beta1';
export const AUTONOMY_PLURAL = 'featureflags';
export const AUTONOMY_NAME =
  process.env.NORTHSTAR_AUTONOMY_FLAG_NAME ?? 'northstar-autonomy';
export const AUTONOMY_NAMESPACE =
  process.env.NORTHSTAR_AUTONOMY_NAMESPACE ?? 'northstar-platform';

// Index 1 of the targeting `if` array -- reproduced verbatim from
// tests/harness/contract.py::TARGETING_THEN_PATH, the harness M5 already
// wrote for exactly this field, so the card's write and the test harness's
// write are provably the same operation on the same path.
export const TARGETING_THEN_PATH =
  '/spec/flagSpec/flags/autonomy-level/targeting/if/1';

// design §4's "auditable and distinguishable from a kubectl edit": every
// write this client makes carries this fieldManager, which lands in the
// object's own `metadata.managedFields[].manager` -- a `kubectl edit` or
// `kubectl patch` never produces this string (kubectl's own field managers
// are named `kubectl-edit` / `kubectl-client-side-apply` / `kubectl-patch`).
//
// CORRECTION (M6 task-3 review, M6/M1-R10): `managedFields` records CURRENT
// OWNERSHIP of a field, not a history of who has ever touched it. Measured:
// after this client's write, the prior `manager: kubectl-patch` entry for
// this same field is GONE, not merely superseded alongside the new one --
// and a subsequent `kubectl patch` of the same field erases THIS manager's
// entry in turn. So this string proves who wrote the field last, at the
// moment you read it; it is not the durable audit trail design §4's
// auditability clause needs. That durable record is the OTLP span this
// module's telemetry.ts emits -- see its own header comment, and design
// §10.
export const FIELD_MANAGER = 'backstage-autonomy-card';

const DEFAULT_TOKEN_PATH =
  process.env.NORTHSTAR_AUTONOMY_WRITER_TOKEN_PATH ??
  '/var/run/secrets/northstar/autonomy-writer/token';
const DEFAULT_CA_PATH =
  process.env.NORTHSTAR_AUTONOMY_WRITER_CA_PATH ??
  '/var/run/secrets/northstar/autonomy-writer/ca.crt';

export function buildInClusterWriterKubeConfig(
  tokenPath: string = DEFAULT_TOKEN_PATH,
  caPath: string = DEFAULT_CA_PATH,
): KubeConfig {
  const host = process.env.KUBERNETES_SERVICE_HOST;
  const port = process.env.KUBERNETES_SERVICE_PORT_HTTPS ?? process.env.KUBERNETES_SERVICE_PORT;
  if (!host || !port) {
    throw new Error(
      'KUBERNETES_SERVICE_HOST/PORT are not set -- this only runs inside ' +
        'the cluster, against the API server every pod is handed by default',
    );
  }
  const token = fs.readFileSync(tokenPath, 'utf8').trim();

  const kc = new KubeConfig();
  kc.loadFromClusterAndUser(
    {
      name: 'northstar-in-cluster',
      server: `https://${host}:${port}`,
      caFile: caPath,
      skipTLSVerify: false,
    },
    {
      name: 'backstage-autonomy-writer',
      token,
    },
  );
  return kc;
}

/** The narrow slice of `@kubernetes/client-node`'s CustomObjectsApi this
 * module calls -- an interface rather than the concrete class so unit tests
 * can substitute a fake without touching a real cluster. */
export interface CustomObjectsClient {
  getNamespacedCustomObject(request: {
    group: string;
    version: string;
    namespace: string;
    plural: string;
    name: string;
  }): Promise<unknown>;
  patchNamespacedCustomObject(request: {
    group: string;
    version: string;
    namespace: string;
    plural: string;
    name: string;
    body: unknown;
    fieldManager?: string;
  }): Promise<unknown>;
}

export function makeCustomObjectsClient(kc: KubeConfig): CustomObjectsClient {
  return kc.makeApiClient(CustomObjectsApi);
}

export class AutonomyContractClient {
  constructor(private readonly api: CustomObjectsClient) {}

  /** Reads the live object. Never a cached copy -- design §4/§5.3. */
  async getFlag(): Promise<unknown> {
    return this.api.getNamespacedCustomObject({
      group: AUTONOMY_GROUP,
      version: AUTONOMY_VERSION,
      namespace: AUTONOMY_NAMESPACE,
      plural: AUTONOMY_PLURAL,
      name: AUTONOMY_NAME,
    });
  }

  /**
   * The one write this whole plugin can perform: replace the targeting
   * `if[1]` branch with `variant`. `variant` is typed as the literal union
   * so nothing upstream of this call can smuggle an arbitrary JSON Patch
   * through -- the router validates the request body against this same
   * union before it ever reaches here (see router.ts).
   */
  async patchThenBranch(variant: 'assisted' | 'autonomous'): Promise<unknown> {
    const patch = [
      { op: 'replace', path: TARGETING_THEN_PATH, value: variant },
    ];
    return this.api.patchNamespacedCustomObject({
      group: AUTONOMY_GROUP,
      version: AUTONOMY_VERSION,
      namespace: AUTONOMY_NAMESPACE,
      plural: AUTONOMY_PLURAL,
      name: AUTONOMY_NAME,
      body: patch,
      fieldManager: FIELD_MANAGER,
    });
  }
}
