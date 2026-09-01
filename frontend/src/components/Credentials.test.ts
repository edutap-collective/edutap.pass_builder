import { describe, expect, it } from "vitest";

import { buildBody } from "./Credentials";

const base = {
  label: "LMU pass type id",
  commonName: "pass.de.lmu.wallet",
  privateKey: "-----BEGIN PRIVATE KEY-----",
  certificate: "-----BEGIN CERTIFICATE-----",
  issuerId: "3388000000000000000",
  serviceAccount: '{"type":"service_account"}',
};

describe("buildBody", () => {
  // One endpoint takes three shapes and answers 400 for a combination it
  // cannot read, so a wrong body here produces a rejection whose cause is not
  // on the screen.
  it("sends only a common name when generating an Apple key", () => {
    const body = buildBody({ ...base, mode: "apple-generate" });
    expect(body).toEqual({
      provider: "apple",
      label: base.label,
      common_name: base.commonName,
    });
  });

  it("sends key and certificate when importing an Apple credential", () => {
    const body = buildBody({ ...base, mode: "apple-import" });
    expect(body).toMatchObject({
      provider: "apple",
      private_key: base.privateKey,
      certificate: base.certificate,
    });
    expect(body).not.toHaveProperty("common_name");
  });

  it("parses the service account rather than passing the text through", () => {
    // The endpoint takes an object, and a paste with a trailing comma should
    // fail where the person can see it.
    const body = buildBody({ ...base, mode: "google-import" });
    expect(body.service_account_json).toEqual({ type: "service_account" });
  });

  it("throws on service account text that is not JSON", () => {
    expect(() =>
      buildBody({ ...base, mode: "google-import", serviceAccount: "{oops," }),
    ).toThrow();
  });
});
