import { describe, expect, it } from "vitest";

import { readDesignerTrio } from "./Templates";

function file(name: string, body: unknown): File {
  return new File([JSON.stringify(body)], name, { type: "application/json" });
}

describe("readDesignerTrio", () => {
  const classJson = { id: "ISSUER.class" };
  const objectJson = { id: "ISSUER.specimen" };
  const mappings = {
    rules: [
      {
        target_kind: "json_pointer",
        target: "/cardTitle/defaultValue/value",
        source_field: "person.display_name",
        value_type: "text",
        required: true,
        default_value: null,
        position: 0,
      },
    ],
    unknown_fields: [],
  };

  it("reads the three files whatever order they were picked in", async () => {
    // A file picker returns them in whatever order the operating system feels
    // like, and a person selecting three files should not have to care.
    const trio = await readDesignerTrio([
      file("mappings.json", mappings),
      file("object.json", objectJson),
      file("class.json", classJson),
    ]);
    expect(trio.classJson).toEqual(classJson);
    expect(trio.objectJson).toEqual(objectJson);
    expect(trio.rules).toHaveLength(1);
  });

  it("takes the mapping file as it stands, extra key and all", async () => {
    // `unknown_fields` is part of the designer's export and not part of the
    // request model. The service ignores it, which is what lets the file be
    // posted without being rewritten on the way.
    const trio = await readDesignerTrio([
      file("class.json", classJson),
      file("object.json", objectJson),
      file("mappings.json", mappings),
    ]);
    expect(trio.rules?.[0]).toEqual(mappings.rules[0]);
  });

  it("accepts a design without a mapping file", async () => {
    const trio = await readDesignerTrio([
      file("class.json", classJson),
      file("object.json", objectJson),
    ]);
    expect(trio.rules).toBeNull();
  });

  it("refuses a selection missing object.json", async () => {
    await expect(
      readDesignerTrio([file("class.json", classJson)]),
    ).rejects.toBeDefined();
  });
});
