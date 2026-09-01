import { describe, expect, it } from "vitest";

import { problemText } from "./client";

describe("problemText", () => {
  it("keeps every finding rather than only the first", () => {
    // Publishing answers 422 with all of them at once. Collapsing that to one
    // line is how a person ends up fixing one problem per attempt.
    const text = problemText({
      title: "Validation failed",
      findings: ["missing required asset: icon.png", "unknown field: person.x"],
    });
    expect(text).toContain("missing required asset: icon.png");
    expect(text).toContain("unknown field: person.x");
  });

  it("prefers the detail over the title", () => {
    expect(problemText({ title: "Conflict", detail: "Only draft versions" })).toBe(
      "Only draft versions",
    );
  });

  it("says something for an error it does not recognise", () => {
    expect(problemText(undefined)).toBe("Unknown error");
  });
});
