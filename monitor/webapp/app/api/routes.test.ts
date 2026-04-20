import test from "node:test";
import assert from "node:assert/strict";

import * as actionsRoute from "./actions/route";
import * as configRoute from "./config/route";
import * as domainRecordsRoute from "./domain-records/route";
import * as githubSecretsRoute from "./github-secrets/route";
import * as overviewRoute from "./overview/route";
import * as siteSettingsRoute from "./site-settings/route";
import {
  adminRequest,
  createStatusWebappFixture,
  readJson,
  readLog,
  withEnv,
  withMockedFetch,
} from "@/test-utils/status-webapp-fixtures";

type TestSnapshot = {
  summary: {
    totalSites: number;
  };
  applications: Array<{
    name: string;
    repoUrl?: string | null;
    webhookRepo?: string | null;
    branch?: string | null;
  }>;
};

type TestConfigDocument = {
  path: string;
  kind: "registry" | "monitor";
  raw: string;
};

type TestConfigMutationResponse = {
  config: TestConfigDocument;
  snapshot: TestSnapshot;
};

type TestActionResponse = {
  result: {
    action: string;
    target: string | null;
  };
  snapshot: TestSnapshot;
};

type TestSecretsDocument = {
  secrets: Array<{
    name: string;
  }>;
};

type TestMutationResponse = {
  result: {
    action: string;
  };
};

type TestDomainRecordsDocument = {
  records: Array<{
    content: string;
  }>;
};

test("admin API routes reject requests without the configured token", async () => {
  await withEnv({ STATUS_WEBAPP_ADMIN_TOKEN: "test-token" }, async () => {
    const requests: Array<Promise<Response>> = [
      configRoute.GET(new Request("http://example.test/api/config")),
      configRoute.PUT(
        new Request("http://example.test/api/config", {
          method: "PUT",
          body: JSON.stringify({ raw: "[]" }),
        })
      ),
      actionsRoute.POST(
        new Request("http://example.test/api/actions", {
          method: "POST",
          body: JSON.stringify({ action: "reload-nginx" }),
        })
      ),
      siteSettingsRoute.PUT(
        new Request("http://example.test/api/site-settings", {
          method: "PUT",
          body: JSON.stringify({ siteName: "app" }),
        })
      ),
      githubSecretsRoute.GET(new Request("http://example.test/api/github-secrets?siteName=app")),
      githubSecretsRoute.PUT(
        new Request("http://example.test/api/github-secrets", {
          method: "PUT",
          body: JSON.stringify({ siteName: "app", name: "API_KEY", value: "secret" }),
        })
      ),
      githubSecretsRoute.DELETE(
        new Request("http://example.test/api/github-secrets", {
          method: "DELETE",
          body: JSON.stringify({ siteName: "app", name: "API_KEY" }),
        })
      ),
      domainRecordsRoute.GET(new Request("http://example.test/api/domain-records?siteName=app")),
      domainRecordsRoute.POST(
        new Request("http://example.test/api/domain-records", {
          method: "POST",
          body: JSON.stringify({
            siteName: "app",
            type: "A",
            name: "app",
            content: "203.0.113.10",
            ttl: 600,
          }),
        })
      ),
      domainRecordsRoute.PUT(
        new Request("http://example.test/api/domain-records", {
          method: "PUT",
          body: JSON.stringify({
            siteName: "app",
            id: "1",
            type: "A",
            name: "app",
            content: "203.0.113.11",
            ttl: 600,
          }),
        })
      ),
      domainRecordsRoute.DELETE(
        new Request("http://example.test/api/domain-records", {
          method: "DELETE",
          body: JSON.stringify({ siteName: "app", id: "1" }),
        })
      ),
    ];

    for (const response of await Promise.all(requests)) {
      assert.equal(response.status, 401);
      assert.match(await response.text(), /Admin controls are locked/);
    }
  });
});

test("status webapp API routes cover config, actions, site settings, secrets, DNS, and overview", async () => {
  const fixture = await createStatusWebappFixture();

  await withMockedFetch(async () =>
    withEnv(fixture.env, async () => {
      const overviewResponse = await overviewRoute.GET();
      assert.equal(overviewResponse.status, 200);
      const overview = await readJson<TestSnapshot>(overviewResponse);
      assert.equal(overview.summary.totalSites, 1);
      assert.equal(overview.applications[0]?.name, "app");

      const configResponse = await configRoute.GET(adminRequest("http://example.test/api/config"));
      assert.equal(configResponse.status, 200);
      const config = await readJson<TestConfigDocument>(configResponse);
      assert.equal(config.kind, "registry");
      assert.equal(config.path, fixture.configPath);
      assert.match(config.raw, /"name": "app"/);

      const saveConfigResponse = await configRoute.PUT(
        adminRequest("http://example.test/api/config", {
          method: "PUT",
          body: JSON.stringify({ raw: config.raw }),
        })
      );
      assert.equal(saveConfigResponse.status, 200);
      const savedConfig = await readJson<TestConfigMutationResponse>(saveConfigResponse);
      assert.equal(savedConfig.config.kind, "registry");
      assert.equal(savedConfig.snapshot.applications[0]?.name, "app");

      const siteSettingsResponse = await siteSettingsRoute.PUT(
        adminRequest("http://example.test/api/site-settings", {
          method: "PUT",
          body: JSON.stringify({
            siteName: "app",
            repoUrl: "https://github.com/example/edited-app.git",
            webhookRepo: "",
            branch: "release",
            checkoutPath: "/srv/apps/edited-app",
          }),
        })
      );
      assert.equal(siteSettingsResponse.status, 200);
      const siteSettings = await readJson<TestConfigMutationResponse>(siteSettingsResponse);
      assert.equal(siteSettings.snapshot.applications[0]?.repoUrl, "https://github.com/example/edited-app.git");
      assert.equal(siteSettings.snapshot.applications[0]?.webhookRepo, "example/edited-app");
      assert.equal(siteSettings.snapshot.applications[0]?.branch, "release");

      const simpleActions = [
        "start-nginx",
        "restart-nginx",
        "reload-nginx",
        "restart-webhook",
        "restart-status-webapp",
      ] as const;
      for (const action of simpleActions) {
        const response = await actionsRoute.POST(
          adminRequest("http://example.test/api/actions", {
            method: "POST",
            body: JSON.stringify({ action }),
          })
        );
        assert.equal(response.status, 200);
        assert.equal((await readJson<TestActionResponse>(response)).result.action, action);
      }

      const restartSiteResponse = await actionsRoute.POST(
        adminRequest("http://example.test/api/actions", {
          method: "POST",
          body: JSON.stringify({ action: "restart-site-service", siteName: "app" }),
        })
      );
      assert.equal(restartSiteResponse.status, 200);
      assert.equal((await readJson<TestActionResponse>(restartSiteResponse)).result.target, "app.service");

      const retryDeployResponse = await actionsRoute.POST(
        adminRequest("http://example.test/api/actions", {
          method: "POST",
          body: JSON.stringify({ action: "retry-deploy", siteName: "app" }),
        })
      );
      assert.equal(retryDeployResponse.status, 200);
      assert.equal((await readJson<TestActionResponse>(retryDeployResponse)).result.action, "retry-deploy");

      const addSiteResponse = await actionsRoute.POST(
        adminRequest("http://example.test/api/actions", {
          method: "POST",
          body: JSON.stringify({
            action: "add-site",
            repoUrl: "https://github.com/example/new-app.git",
            branch: "main",
            checkoutPath: "/srv/apps/new-app",
            email: "ops@example.com",
            skipGithubHook: true,
          }),
        })
      );
      assert.equal(addSiteResponse.status, 200);
      const addSite = await readJson<TestActionResponse>(addSiteResponse);
      assert.equal(addSite.result.action, "add-site");
      assert.ok(addSite.snapshot.applications.some((application: { name: string }) => application.name === "new-app"));

      const secretsResponse = await githubSecretsRoute.GET(
        adminRequest("http://example.test/api/github-secrets?siteName=app")
      );
      assert.equal(secretsResponse.status, 200);
      assert.equal((await readJson<TestSecretsDocument>(secretsResponse)).secrets[0]?.name, "API_KEY");

      const setSecretResponse = await githubSecretsRoute.PUT(
        adminRequest("http://example.test/api/github-secrets", {
          method: "PUT",
          body: JSON.stringify({ siteName: "app", name: "API_KEY", value: "secret-value" }),
        })
      );
      assert.equal(setSecretResponse.status, 200);
      assert.equal((await readJson<TestMutationResponse>(setSecretResponse)).result.action, "set");

      const deleteSecretResponse = await githubSecretsRoute.DELETE(
        adminRequest("http://example.test/api/github-secrets", {
          method: "DELETE",
          body: JSON.stringify({ siteName: "app", name: "API_KEY" }),
        })
      );
      assert.equal(deleteSecretResponse.status, 200);
      assert.equal((await readJson<TestMutationResponse>(deleteSecretResponse)).result.action, "delete");

      const recordsResponse = await domainRecordsRoute.GET(
        adminRequest("http://example.test/api/domain-records?siteName=app")
      );
      assert.equal(recordsResponse.status, 200);
      assert.equal((await readJson<TestDomainRecordsDocument>(recordsResponse)).records[0]?.content, "203.0.113.10");

      const createRecordResponse = await domainRecordsRoute.POST(
        adminRequest("http://example.test/api/domain-records", {
          method: "POST",
          body: JSON.stringify({
            siteName: "app",
            type: "A",
            name: "app",
            content: "203.0.113.10",
            ttl: 600,
          }),
        })
      );
      assert.equal(createRecordResponse.status, 200);
      assert.equal((await readJson<TestMutationResponse>(createRecordResponse)).result.action, "create");

      const updateRecordResponse = await domainRecordsRoute.PUT(
        adminRequest("http://example.test/api/domain-records", {
          method: "PUT",
          body: JSON.stringify({
            siteName: "app",
            id: "1",
            type: "A",
            name: "app",
            content: "203.0.113.11",
            ttl: 600,
          }),
        })
      );
      assert.equal(updateRecordResponse.status, 200);
      assert.equal((await readJson<TestMutationResponse>(updateRecordResponse)).result.action, "update");

      const deleteRecordResponse = await domainRecordsRoute.DELETE(
        adminRequest("http://example.test/api/domain-records", {
          method: "DELETE",
          body: JSON.stringify({ siteName: "app", id: "1" }),
        })
      );
      assert.equal(deleteRecordResponse.status, 200);
      assert.equal((await readJson<TestMutationResponse>(deleteRecordResponse)).result.action, "delete");
    })
  );

  const systemctlLog = await readLog(fixture.logsDir, "systemctl.log");
  assert.match(systemctlLog, /^start nginx$/m);
  assert.match(systemctlLog, /^restart nginx$/m);
  assert.match(systemctlLog, /^reload nginx$/m);
  assert.match(systemctlLog, /^restart site-webhook-receiver\.service$/m);
  assert.match(systemctlLog, /^restart server-setup-status-webapp\.service$/m);
  assert.match(systemctlLog, /^restart app\.service$/m);

  const pythonLog = await readLog(fixture.logsDir, "python3.log");
  assert.match(pythonLog, /deploy_repo\.py --repo-url https:\/\/github\.com\/example\/edited-app\.git/);
  assert.match(pythonLog, /deploy_repo\.py --repo-url https:\/\/github\.com\/example\/new-app\.git/);
  assert.match(pythonLog, /manage_github_secrets\.py set API_KEY --site app --json/);
  assert.match(pythonLog, /manage_github_secrets\.py delete API_KEY --site app --json/);
  assert.match(pythonLog, /manage_dns_records\.py --registry .* --json create --site app --type A --name app --content 203\.0\.113\.10 --ttl 600/);
  assert.match(pythonLog, /manage_dns_records\.py --registry .* --json update --site app --id 1 --type A --name app --content 203\.0\.113\.11 --ttl 600/);
  assert.match(pythonLog, /manage_dns_records\.py --registry .* --json delete --site app --id 1/);
});
