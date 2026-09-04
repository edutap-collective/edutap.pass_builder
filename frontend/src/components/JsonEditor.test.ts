import { describe, expect, it } from "vitest";

import { tokenize } from "./JsonEditor";

/**
 * The tokenizer sits under an editable textarea, so the one property that must
 * hold is that it never changes the text. A dropped or added character moves
 * every glyph after it away from the caret above it.
 */
function roundTrip(source: string): string {
  return tokenize(source)
    .map((token) => token.text)
    .join("");
}

describe("tokenize", () => {
  it("gives back exactly the text it was given", () => {
    const source = '{\n  "id": "3388000000022",\n  "n": -1.5e3,\n  "ok": true\n}';
    expect(roundTrip(source)).toBe(source);
  });

  it("keeps a half-typed string intact", () => {
    // The normal state of a field somebody is typing into. Dropping the
    // unterminated quote here would shift the rest of the document.
    expect(roundTrip('{"issuerName": "LMU M')).toBe('{"issuerName": "LMU M');
  });

  it("survives text that is not JSON at all", () => {
    expect(roundTrip("nonsense, ohne Struktur")).toBe("nonsense, ohne Struktur");
  });

  it("tells a key from a value", () => {
    const tokens = tokenize('{"issuerName": "LMU"}');
    const strings = tokens.filter((t) => t.kind === "key" || t.kind === "string");
    expect(strings.map((t) => [t.kind, t.text])).toEqual([
      ["key", '"issuerName"'],
      ["string", '"LMU"'],
    ]);
  });

  it("still reads a key when whitespace precedes the colon", () => {
    const tokens = tokenize('{"a"\n  : 1}');
    expect(tokens[1]).toEqual({ text: '"a"', kind: "key" });
  });

  it("does not mistake a string in an array for a key", () => {
    const tokens = tokenize('["a", "b"]');
    expect(tokens.every((t) => t.kind !== "key")).toBe(true);
  });
});
