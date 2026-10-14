import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderInTestApp, TestApiProvider } from '@backstage/test-utils';
import { discoveryApiRef, fetchApiRef } from '@backstage/frontend-plugin-api';
import { AutonomyContractPage } from './AutonomyContractPage';

const STATUS_ASSISTED = {
  flag: 'northstar-autonomy',
  namespace: 'northstar-platform',
  state: 'ENABLED',
  defaultVariant: 'assisted',
  perIdentity: {
    'settlement-agent': { level: 'assisted', branch: 'targeting-then' },
  },
};

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'status',
    json: async () => body,
  };
}

function renderCard(fetchImpl: jest.Mock) {
  return renderInTestApp(
    <TestApiProvider
      apis={[
        [
          discoveryApiRef,
          { getBaseUrl: async () => 'http://localhost:7007/api/autonomy-contract' },
        ],
        [fetchApiRef, { fetch: fetchImpl }],
      ]}
    >
      <AutonomyContractPage />
    </TestApiProvider>,
  );
}

describe('AutonomyContractPage', () => {
  it('reads live status on mount and shows the identity', async () => {
    const fetchImpl = jest.fn(async () => jsonResponse(200, STATUS_ASSISTED));

    await renderCard(fetchImpl);

    await waitFor(() => {
      expect(screen.getByText("settlement-agent")).toBeInTheDocument();
    });
    expect(screen.queryByText(/senior-settlement-agent/)).not.toBeInTheDocument();
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:7007/api/autonomy-contract/status',
    );
    // One "assisted" chip for the identity plus the defaultVariant chip:
    // this is the fail-closed shipped state, not a guess.
    expect(screen.getAllByText('assisted').length).toBeGreaterThanOrEqual(2);
  });

  it('posts exactly {variant: "autonomous"} when the autonomous button is clicked, and nothing else', async () => {
    const fetchImpl = jest
      .fn()
      // initial GET /status
      .mockResolvedValueOnce(jsonResponse(200, STATUS_ASSISTED))
      // POST /variant
      .mockResolvedValueOnce(
        jsonResponse(200, {
          previousVariant: 'assisted',
          newVariant: 'autonomous',
          telemetryEmitted: true,
        }),
      )
      // reload GET /status after the write
      .mockResolvedValueOnce(
        jsonResponse(200, {
          ...STATUS_ASSISTED,
          perIdentity: {
            ...STATUS_ASSISTED.perIdentity,
            'settlement-agent': { level: 'autonomous', branch: 'targeting-then' },
          },
        }),
      );

    await renderCard(fetchImpl);
    await waitFor(() => screen.getByText("settlement-agent"));

    fireEvent.click(screen.getByText(/Set settlement-agent: autonomous/));

    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalledTimes(3);
    });
    const [writeUrl, writeInit] = fetchImpl.mock.calls[1];
    expect(writeUrl).toBe('http://localhost:7007/api/autonomy-contract/variant');
    expect(writeInit.method).toBe('POST');
    expect(JSON.parse(writeInit.body)).toEqual({ variant: 'autonomous' });
  });

  it('shows a warning, not a failure, when the write succeeds but telemetry export did not', async () => {
    const fetchImpl = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, STATUS_ASSISTED))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          previousVariant: 'assisted',
          newVariant: 'autonomous',
          telemetryEmitted: false,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, STATUS_ASSISTED));

    await renderCard(fetchImpl);
    await waitFor(() => screen.getByText("settlement-agent"));
    fireEvent.click(screen.getByText(/Set settlement-agent: autonomous/));

    await waitFor(() => {
      expect(screen.getByText(/audit span could not be delivered/)).toBeInTheDocument();
    });
  });

  it('surfaces a refused write as an error rather than pretending it succeeded', async () => {
    const fetchImpl = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, STATUS_ASSISTED))
      .mockResolvedValueOnce(
        jsonResponse(400, {
          error: 'request body\'s "variant" must be one of assisted, autonomous',
        }),
      );

    await renderCard(fetchImpl);
    await waitFor(() => screen.getByText("settlement-agent"));
    fireEvent.click(screen.getByText(/Set settlement-agent: autonomous/));

    await waitFor(() => {
      expect(screen.getAllByText(/the write was refused/).length).toBeGreaterThan(0);
    });
  });
});
