// Writes the management application's OpenAPI document to frontend/openapi.json
// by asking the application for it. No server has to be running: FastAPI
// produces its schema in-process.
//
// The two settings below are the ones `Settings` has no default for. They are
// throwaway values -- nothing is rendered, nothing is signed, and the schema
// does not depend on them.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";

const script = `
import json
from edutap.pass_builder.ui.app import create_ui_app
print(json.dumps(create_ui_app().openapi(), indent=2, sort_keys=True))
`;

const schema = execFileSync("uv", ["run", "python", "-c", script], {
  cwd: "..",
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024,
  env: {
    ...process.env,
    EDUTAP_PASS_BUILDER_SECRET_MASTER_KEY: randomBytes(32).toString("base64"),
    EDUTAP_PASS_BUILDER_DATA_PROVIDER_BASE_URL: "http://data-provider.invalid",
  },
});

writeFileSync("openapi.json", schema);
console.log(`wrote openapi.json (${schema.length} bytes)`);
