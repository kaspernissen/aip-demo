/**
 * M6 Task 3: the autonomy card itself. Design §5.3/§4: shows, per agent
 * identity, the level `northstar-autonomy` currently resolves to -- read
 * live from the backend's `/api/autonomy-contract/status`, never cached in
 * this component's own state beyond one render -- and offers the one write
 * this whole plugin can make: flip `settlement-agent`'s targeting branch
 * between `assisted` and `autonomous`.
 *
 * Talks only to `platform/backstage/packages/backend/src/plugins/
 * autonomyContract`'s two routes. It never constructs a Kubernetes API call,
 * a resource name, or a JSON Patch path itself -- those are fixed, on the
 * backend, in `k8sClient.ts`. This component is a view over that plugin, not
 * a second place any of design §4's constraints could be re-litigated.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@material-ui/core';
import {
  Content,
  Header,
  InfoCard,
  Page,
  Progress,
  ResponseErrorPanel,
} from '@backstage/core-components';
import { discoveryApiRef, fetchApiRef, useApi } from '@backstage/frontend-plugin-api';

interface IdentityStatus {
  level: string;
  branch: string;
}

interface StatusResponse {
  flag: string;
  namespace: string;
  state: string;
  defaultVariant: string;
  perIdentity: Record<string, IdentityStatus>;
}

// Matches router.ts's TARGETED_IDENTITIES keys exactly -- only used here to
// give the two rows a friendlier label, never to decide which identities
// exist (that stays entirely the backend's own list).
const IDENTITY_LABELS: Record<string, string> = {
  'settlement-agent': 'settlement-agent (junior)',
  'senior-settlement-agent': 'senior-settlement-agent',
};

const WRITABLE_IDENTITY = 'settlement-agent';

async function readJsonError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.error === 'string' ? body.error : res.statusText;
  } catch {
    return res.statusText;
  }
}

export function AutonomyContractPage() {
  const discoveryApi = useApi(discoveryApiRef);
  const fetchApi = useApi(fetchApiRef);

  const [status, setStatus] = useState<StatusResponse | undefined>();
  const [error, setError] = useState<Error | undefined>();
  const [loading, setLoading] = useState(true);
  const [writing, setWriting] = useState(false);
  const [telemetryWarning, setTelemetryWarning] = useState<string | undefined>();

  const load = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const baseUrl = await discoveryApi.getBaseUrl('autonomy-contract');
      const res = await fetchApi.fetch(`${baseUrl}/status`);
      if (!res.ok) {
        throw new Error(`could not read the contract (HTTP ${res.status}): ${await readJsonError(res)}`);
      }
      setStatus(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setLoading(false);
    }
  }, [discoveryApi, fetchApi]);

  useEffect(() => {
    load();
  }, [load]);

  const setVariant = useCallback(
    async (variant: 'assisted' | 'autonomous') => {
      setWriting(true);
      setError(undefined);
      setTelemetryWarning(undefined);
      try {
        const baseUrl = await discoveryApi.getBaseUrl('autonomy-contract');
        const res = await fetchApi.fetch(`${baseUrl}/variant`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ variant }),
        });
        if (!res.ok) {
          throw new Error(`the write was refused (HTTP ${res.status}): ${await readJsonError(res)}`);
        }
        const body = await res.json();
        if (body.telemetryEmitted === false) {
          setTelemetryWarning(
            'the contract was changed, but the audit span could not be ' +
              'delivered to the OTLP collector -- see the backstage pod\'s ' +
              'own logs for the reason.',
          );
        }
        await load();
      } catch (e) {
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        setWriting(false);
      }
    },
    [discoveryApi, fetchApi, load],
  );

  const juniorLevel = status?.perIdentity[WRITABLE_IDENTITY]?.level;

  return (
    <Page themeId="tool">
      <Header
        title="Autonomy contract"
        subtitle="northstar-autonomy, read live from the FeatureFlag -- never a cached copy"
      />
      <Content>
        <Typography variant="body2" paragraph>
          This card edits the <code>northstar-autonomy</code> FeatureFlag
          through its own narrow ServiceAccount (get/list/patch, restricted to
          this one resource -- design §4). It does not decide anything: the
          Policy Decision Service is still the only decision point, and this
          card only ever changes the resource the PDS already reads.
        </Typography>
        {loading && <Progress />}
        {error && <ResponseErrorPanel error={error} />}
        {status && (
          <InfoCard title={`${status.flag} (namespace: ${status.namespace})`}>
            <Typography variant="body2" gutterBottom>
              flag state:{' '}
              <Chip size="small" label={status.state} /> &nbsp; fail-closed
              default:{' '}
              <Chip size="small" label={status.defaultVariant} />
            </Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Identity</TableCell>
                  <TableCell>Resolved level</TableCell>
                  <TableCell>Branch</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {Object.entries(status.perIdentity).map(([id, identity]) => (
                  <TableRow key={id}>
                    <TableCell>{IDENTITY_LABELS[id] ?? id}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={identity.level}
                        color={identity.level === 'autonomous' ? 'secondary' : 'default'}
                      />
                    </TableCell>
                    <TableCell>
                      <code>{identity.branch}</code>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {telemetryWarning && (
              <Typography variant="body2" color="error" style={{ marginTop: 12 }}>
                {telemetryWarning}
              </Typography>
            )}
            <div style={{ marginTop: 16 }}>
              <Typography variant="body2" gutterBottom>
                The one write this card can make: {WRITABLE_IDENTITY}'s
                targeting branch, assisted ⇄ autonomous.
              </Typography>
              <Button
                variant="contained"
                color="secondary"
                disabled={writing || juniorLevel === 'autonomous'}
                onClick={() => setVariant('autonomous')}
                style={{ marginRight: 8 }}
              >
                Set {WRITABLE_IDENTITY}: autonomous
              </Button>
              <Button
                variant="outlined"
                disabled={writing || juniorLevel === 'assisted'}
                onClick={() => setVariant('assisted')}
              >
                Set {WRITABLE_IDENTITY}: assisted
              </Button>
            </div>
          </InfoCard>
        )}
      </Content>
    </Page>
  );
}
