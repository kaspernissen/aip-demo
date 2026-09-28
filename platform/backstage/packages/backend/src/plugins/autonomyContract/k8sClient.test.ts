import {
  AUTONOMY_GROUP,
  AUTONOMY_NAME,
  AUTONOMY_NAMESPACE,
  AUTONOMY_PLURAL,
  AUTONOMY_VERSION,
  AutonomyContractClient,
  CustomObjectsClient,
  FIELD_MANAGER,
  TARGETING_THEN_PATH,
  buildInClusterWriterKubeConfig,
} from './k8sClient';

describe('AutonomyContractClient', () => {
  function fakeApi(): CustomObjectsClient & {
    getNamespacedCustomObject: jest.Mock;
    patchNamespacedCustomObject: jest.Mock;
  } {
    return {
      getNamespacedCustomObject: jest.fn(async () => ({ fake: true })),
      patchNamespacedCustomObject: jest.fn(async () => ({ patched: true })),
    };
  }

  it('reads exactly the one flag this repo has, by group/version/namespace/plural/name', async () => {
    const api = fakeApi();
    const client = new AutonomyContractClient(api);

    await client.getFlag();

    expect(api.getNamespacedCustomObject).toHaveBeenCalledWith({
      group: AUTONOMY_GROUP,
      version: AUTONOMY_VERSION,
      namespace: AUTONOMY_NAMESPACE,
      plural: AUTONOMY_PLURAL,
      name: AUTONOMY_NAME,
    });
    // The values a loosened RBAC Role could not, by itself, redirect this
    // client toward -- pinned here so a future edit that parameterises any
    // of them (e.g. reading a namespace from a request) is a visible diff.
    expect(AUTONOMY_GROUP).toBe('core.openfeature.dev');
    expect(AUTONOMY_PLURAL).toBe('featureflags');
  });

  it('patches ONLY the targeting.if[1] path, with the distinguishing fieldManager, and no other field', async () => {
    const api = fakeApi();
    const client = new AutonomyContractClient(api);

    await client.patchThenBranch('autonomous');

    expect(api.patchNamespacedCustomObject).toHaveBeenCalledTimes(1);
    const call = api.patchNamespacedCustomObject.mock.calls[0][0];
    expect(call.group).toBe(AUTONOMY_GROUP);
    expect(call.version).toBe(AUTONOMY_VERSION);
    expect(call.namespace).toBe(AUTONOMY_NAMESPACE);
    expect(call.plural).toBe(AUTONOMY_PLURAL);
    expect(call.name).toBe(AUTONOMY_NAME);
    expect(call.fieldManager).toBe(FIELD_MANAGER);
    expect(call.fieldManager).not.toMatch(/^kubectl/);
    expect(call.body).toEqual([
      { op: 'replace', path: TARGETING_THEN_PATH, value: 'autonomous' },
    ]);
    // Exactly one JSON Patch operation -- never a whole-object replace, and
    // never a second operation touching anything else in the same call.
    expect(call.body).toHaveLength(1);
    // The literal, independent of the imported constant: comparing the
    // patch body against TARGETING_THEN_PATH alone is tautological (a
    // mutation to the constant moves both sides together and this test
    // would stay green). This pins the actual JSON Pointer string, which
    // must also match tests/harness/contract.py::TARGETING_THEN_PATH byte
    // for byte -- the whole point of patching this path is that the test
    // harness reads it too.
    expect(call.body[0].path).toBe(
      '/spec/flagSpec/flags/autonomy-level/targeting/if/1',
    );
  });
});

describe('buildInClusterWriterKubeConfig', () => {
  const ORIGINAL_ENV = process.env;

  beforeEach(() => {
    process.env = { ...ORIGINAL_ENV };
  });

  afterAll(() => {
    process.env = ORIGINAL_ENV;
  });

  it('refuses to run outside a cluster rather than silently defaulting somewhere', () => {
    delete process.env.KUBERNETES_SERVICE_HOST;
    delete process.env.KUBERNETES_SERVICE_PORT;
    delete process.env.KUBERNETES_SERVICE_PORT_HTTPS;
    expect(() => buildInClusterWriterKubeConfig('/nonexistent/token', '/nonexistent/ca.crt')).toThrow(
      /KUBERNETES_SERVICE_HOST/,
    );
  });
});
